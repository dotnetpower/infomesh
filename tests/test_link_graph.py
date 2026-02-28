"""Tests for infomesh.index.link_graph — link graph and domain authority."""

from __future__ import annotations

import pytest

from infomesh.index.link_graph import LinkGraph


@pytest.fixture
def graph() -> LinkGraph:
    g = LinkGraph(":memory:")
    yield g
    g.close()


class TestLinkGraph:
    def test_add_links_inserts(self, graph: LinkGraph):
        count = graph.add_links(
            "https://a.com/page1",
            ["https://b.com/page1", "https://c.com"],
        )
        assert count == 2

    def test_add_links_dedup(self, graph: LinkGraph):
        graph.add_links("https://a.com/p1", ["https://b.com"])
        count = graph.add_links("https://a.com/p1", ["https://b.com"])  # duplicate
        assert count == 1  # SQL INSERT OR IGNORE → still returns 1 but no error

    def test_add_links_skips_empty_domain(self, graph: LinkGraph):
        count = graph.add_links("https://a.com/p1", ["not-a-url"])
        assert count == 0

    def test_get_stats_empty(self, graph: LinkGraph):
        stats = graph.get_stats()
        assert stats["link_count"] == 0
        assert stats["domain_count"] == 0

    def test_get_stats_after_insert(self, graph: LinkGraph):
        graph.add_links("https://a.com/p1", ["https://b.com/p1", "https://c.com"])
        stats = graph.get_stats()
        assert stats["link_count"] == 2

    def test_extract_domain(self):
        url = "https://www.example.com/path"
        assert LinkGraph._extract_domain(url) == "www.example.com"
        assert LinkGraph._extract_domain("http://EXAMPLE.COM") == "example.com"


class TestDomainAuthority:
    def test_empty_graph_returns_empty(self, graph: LinkGraph):
        result = graph.compute_domain_authority()
        assert result == {}

    def test_single_link_gives_authority(self, graph: LinkGraph):
        graph.add_links("https://a.com/p1", ["https://b.com/p1"])
        scores = graph.compute_domain_authority()
        assert "b.com" in scores
        assert scores["b.com"] > 0

    def test_more_inbound_links_higher_authority(self, graph: LinkGraph):
        """A domain linked to by many others should have higher authority."""
        # Multiple domains link to b.com
        graph.add_links("https://a.com/p1", ["https://b.com"])
        graph.add_links("https://c.com/p1", ["https://b.com"])
        graph.add_links("https://d.com/p1", ["https://b.com"])
        # Only one domain links to e.com
        graph.add_links("https://a.com/p2", ["https://e.com"])

        scores = graph.compute_domain_authority()
        assert scores["b.com"] > scores["e.com"]

    def test_self_links_contribute_less(self, graph: LinkGraph):
        """External inbound links should contribute more authority
        than internal self-links of similar volume."""
        # b.com: receives external links AND has outgoing links (realistic)
        graph.add_links("https://a.com/p1", ["https://b.com/p1"])
        graph.add_links("https://c.com/p1", ["https://b.com/p1"])
        graph.add_links("https://e.com/p1", ["https://b.com/p1"])
        graph.add_links("https://b.com/p1", ["https://a.com/p1"])  # b links back

        # d.com: only has internal self-links, same volume
        graph.add_links("https://d.com/p1", ["https://d.com/p2"])
        graph.add_links("https://d.com/p2", ["https://d.com/p3"])
        graph.add_links("https://d.com/p3", ["https://d.com/p1"])

        scores = graph.compute_domain_authority()
        # b.com with 3 unique external inbound domains beats d.com self-loop
        assert scores["b.com"] > scores.get("d.com", 0)

    def test_domain_authority_lookup(self, graph: LinkGraph):
        graph.add_links("https://a.com/p1", ["https://b.com/p1"])
        graph.compute_domain_authority()

        score = graph.domain_authority("b.com")
        assert 0.0 <= score <= 1.0
        assert score > 0

    def test_domain_authority_unknown_domain(self, graph: LinkGraph):
        assert graph.domain_authority("unknown.com") == 0.0

    def test_url_authority(self, graph: LinkGraph):
        graph.add_links("https://a.com/p1", ["https://b.com/p1"])
        graph.compute_domain_authority()

        score = graph.url_authority("https://b.com/some/page")
        assert score > 0

    def test_url_authority_empty_url(self, graph: LinkGraph):
        assert graph.url_authority("") == 0.0

    def test_scores_normalized_to_one(self, graph: LinkGraph):
        """The maximum domain authority should be 1.0."""
        graph.add_links("https://a.com/p1", ["https://b.com"])
        graph.add_links("https://c.com/p1", ["https://b.com"])
        graph.add_links("https://a.com/p2", ["https://d.com"])

        scores = graph.compute_domain_authority()
        max_score = max(scores.values())
        assert max_score == pytest.approx(1.0, abs=0.001)

    def test_authority_persisted_in_db(self, graph: LinkGraph):
        """compute_domain_authority should persist scores."""
        graph.add_links("https://a.com/p1", ["https://b.com"])
        graph.compute_domain_authority()

        stats = graph.get_stats()
        assert stats["domain_count"] > 0

    def test_recompute_updates_scores(self, graph: LinkGraph):
        """Running compute twice with more data should update."""
        graph.add_links("https://a.com/p1", ["https://b.com"])
        graph.compute_domain_authority()

        # Add more links
        graph.add_links("https://c.com/p1", ["https://b.com"])
        graph.add_links("https://d.com/p1", ["https://b.com"])
        scores2 = graph.compute_domain_authority()

        # b.com should maintain high authority
        assert "b.com" in scores2
