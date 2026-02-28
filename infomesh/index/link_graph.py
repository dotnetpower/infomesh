"""Link graph storage and domain authority computation.

Stores directional link relationships (source → target) discovered
during crawling and computes per-domain authority scores based on
inbound link counts.  Authority scores are used as a ranking signal
alongside BM25 and freshness in the search pipeline.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger()

# Maximum iterations for iterative authority propagation.
_MAX_ITERATIONS = 20

# Convergence threshold for authority propagation.
_CONVERGENCE_THRESHOLD = 1e-6

# Damping factor for simplified PageRank-style propagation.
_DAMPING = 0.85


class LinkGraph:
    """SQLite-backed directional link graph.

    Stores ``(source_url, target_url)`` edges discovered during crawling
    and computes domain-level authority scores from inbound links.

    Usage::

        graph = LinkGraph(db_path="links.db")
        graph.add_links("https://a.com/page", ["https://b.com", "https://c.com"])
        score = graph.domain_authority("b.com")  # 0.0 – 1.0
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ── Schema ──────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT NOT NULL,
                target_url TEXT NOT NULL,
                source_domain TEXT NOT NULL,
                target_domain TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                UNIQUE(source_url, target_url)
            );

            CREATE INDEX IF NOT EXISTS idx_target_domain
                ON links(target_domain);

            CREATE INDEX IF NOT EXISTS idx_source_domain
                ON links(source_domain);

            CREATE TABLE IF NOT EXISTS domain_authority (
                domain TEXT PRIMARY KEY,
                score REAL NOT NULL DEFAULT 0.0,
                inbound_count INTEGER NOT NULL DEFAULT 0,
                outbound_count INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            );
        """)
        self._conn.commit()

    # ── Link management ─────────────────────────────────────────

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract the domain (netloc) from a URL."""
        parsed = urlparse(url)
        return parsed.netloc.lower()

    def add_links(
        self,
        source_url: str,
        target_urls: list[str],
    ) -> int:
        """Record link relationships from a crawled page.

        Self-links (same domain) are stored but weighted less in
        authority computation.

        Args:
            source_url: The page containing the links.
            target_urls: URLs linked from the source page.

        Returns:
            Number of new links inserted (duplicates are skipped).
        """
        source_domain = self._extract_domain(source_url)
        inserted = 0

        for target in target_urls:
            target_domain = self._extract_domain(target)
            if not target_domain:
                continue
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO links
                       (source_url, target_url, source_domain, target_domain)
                       VALUES (?, ?, ?, ?)""",
                    (source_url, target, source_domain, target_domain),
                )
                inserted += 1
            except sqlite3.Error:
                pass

        if inserted:
            self._conn.commit()
            logger.debug(
                "links_stored",
                source=source_url,
                targets=len(target_urls),
                inserted=inserted,
            )

        return inserted

    # ── Domain authority ────────────────────────────────────────

    def compute_domain_authority(self) -> dict[str, float]:
        """Compute domain authority scores from the link graph.

        Uses a simplified PageRank-style iterative propagation:

        1. Count inbound and outbound links per domain.
        2. Initialize each domain's score to ``1/N``.
        3. Iteratively propagate scores from source to target
           domains, weighted by the source domain's outgoing
           link count.
        4. Apply damping and normalize to ``[0, 1]``.

        Cross-domain links contribute full weight, same-domain
        links contribute 10% weight to prevent self-inflation.

        Returns:
            Dictionary of ``{domain: authority_score}``.
        """
        # Step 1: Gather all domains and their link counts
        rows = self._conn.execute("""
            SELECT target_domain, COUNT(DISTINCT source_domain) as inbound,
                   source_domain != target_domain as is_external
            FROM links
            GROUP BY target_domain
        """).fetchall()

        if not rows:
            return {}

        # Get all unique domains
        all_domains_rows = self._conn.execute("""
            SELECT DISTINCT domain FROM (
                SELECT source_domain AS domain FROM links
                UNION
                SELECT target_domain AS domain FROM links
            )
        """).fetchall()
        all_domains = [r["domain"] for r in all_domains_rows]
        n = len(all_domains)

        if n == 0:
            return {}

        # Count outbound links per domain
        outbound = {}
        for row in self._conn.execute("""
            SELECT source_domain, COUNT(DISTINCT target_domain) as cnt
            FROM links
            WHERE source_domain != target_domain
            GROUP BY source_domain
        """).fetchall():
            outbound[row["source_domain"]] = row["cnt"]

        # Step 2: Initialize scores
        scores = {d: 1.0 / n for d in all_domains}

        # Build adjacency: source_domain → [(target_domain, weight)]
        edges: dict[str, list[tuple[str, float]]] = {d: [] for d in all_domains}
        for row in self._conn.execute("""
            SELECT source_domain, target_domain,
                   COUNT(*) as link_count,
                   source_domain != target_domain as is_external
            FROM links
            GROUP BY source_domain, target_domain
        """).fetchall():
            weight = float(row["link_count"])
            if not row["is_external"]:
                weight *= 0.1  # Self-links contribute 10%
            edges[row["source_domain"]].append((row["target_domain"], weight))

        # Step 3: Iterative propagation
        for iteration in range(_MAX_ITERATIONS):
            new_scores = {d: (1.0 - _DAMPING) / n for d in all_domains}

            for src, targets in edges.items():
                total_weight = sum(w for _, w in targets)
                if total_weight == 0:
                    continue
                for tgt, w in targets:
                    contribution = _DAMPING * scores[src] * (w / total_weight)
                    new_scores[tgt] += contribution

            # Check convergence
            diff = sum(abs(new_scores[d] - scores[d]) for d in all_domains)
            scores = new_scores

            if diff < _CONVERGENCE_THRESHOLD:
                logger.debug(
                    "authority_converged",
                    iterations=iteration + 1,
                    diff=diff,
                )
                break

        # Step 4: Normalize to [0, 1]
        max_score = max(scores.values()) if scores else 1.0
        if max_score > 0:
            normalized = {d: s / max_score for d, s in scores.items()}
        else:
            normalized = scores

        # Persist to domain_authority table
        for domain, score in normalized.items():
            out_count = outbound.get(domain, 0)
            in_count = self._conn.execute(
                """SELECT COUNT(DISTINCT source_domain)
                   FROM links
                   WHERE target_domain = ? AND source_domain != ?""",
                (domain, domain),
            ).fetchone()[0]

            self._conn.execute(
                """INSERT OR REPLACE INTO domain_authority
                   (domain, score, inbound_count, outbound_count, updated_at)
                   VALUES (?, ?, ?, ?, strftime('%s', 'now'))""",
                (domain, round(score, 6), in_count, out_count),
            )
        self._conn.commit()

        logger.info("domain_authority_computed", domains=len(normalized))
        return normalized

    def domain_authority(self, domain: str) -> float:
        """Get the authority score for a single domain.

        Returns the cached score from the last
        ``compute_domain_authority()`` run, or 0.0 if unknown.

        Args:
            domain: Domain name (e.g. ``"example.com"``).

        Returns:
            Authority score in ``[0.0, 1.0]``.
        """
        row = self._conn.execute(
            "SELECT score FROM domain_authority WHERE domain = ?",
            (domain.lower(),),
        ).fetchone()
        return row["score"] if row else 0.0

    def url_authority(self, url: str) -> float:
        """Get domain authority for the domain of a given URL.

        Convenience wrapper around ``domain_authority()``.
        """
        domain = self._extract_domain(url)
        return self.domain_authority(domain) if domain else 0.0

    # ── Stats ───────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """Get link graph statistics."""
        link_count = self._conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        domain_count = self._conn.execute(
            "SELECT COUNT(*) FROM domain_authority"
        ).fetchone()[0]
        return {
            "link_count": link_count,
            "domain_count": domain_count,
        }

    # ── Resource management ─────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> LinkGraph:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
