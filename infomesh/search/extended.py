"""Extended search features — batch, caching, multilingual, clustering.

Features:
- #5: Multilingual query translation (keyword-level)
- #26: Batch search API
- #27: Search result summary cache
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


# ── #26: Batch Search ──────────────────────────────────────────────


@dataclass
class BatchQuery:
    """A single query in a batch request."""

    query: str
    top_k: int = 5
    language: str | None = None


@dataclass
class BatchResult:
    """Result of a batch search operation."""

    query: str
    results: list[dict[str, object]] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None


@dataclass
class BatchSearchResponse:
    """Full batch search response."""

    results: list[BatchResult] = field(default_factory=list)
    total_elapsed_ms: float = 0.0
    total_queries: int = 0


async def batch_search(
    queries: list[BatchQuery],
    search_fn: object,
    *,
    max_parallel: int = 5,
) -> BatchSearchResponse:
    """Execute multiple search queries in parallel.

    Args:
        queries: List of queries to execute.
        search_fn: Async search function(query, top_k, language).
        max_parallel: Max concurrent searches.

    Returns:
        BatchSearchResponse with all results.
    """
    import asyncio

    start = time.monotonic()
    semaphore = asyncio.Semaphore(max_parallel)
    response = BatchSearchResponse(total_queries=len(queries))

    async def _one(q: BatchQuery) -> BatchResult:
        async with semaphore:
            t0 = time.monotonic()
            try:
                result = await search_fn(  # type: ignore[operator]
                    q.query,
                    q.top_k,
                    q.language,
                )
                return BatchResult(
                    query=q.query,
                    results=result if isinstance(result, list) else [],
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )
            except Exception as exc:  # noqa: BLE001
                return BatchResult(
                    query=q.query,
                    error=str(exc),
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )

    tasks = [_one(q) for q in queries[:50]]  # Cap at 50
    results = await asyncio.gather(*tasks)
    response.results = list(results)
    response.total_elapsed_ms = (time.monotonic() - start) * 1000

    return response


# ── #27: Search Summary Cache ──────────────────────────────────────


@dataclass
class CachedSummary:
    """A cached LLM-generated summary."""

    query_hash: str
    summary: str
    sources: list[str]
    created_at: float
    expires_at: float


class SummaryCache:
    """In-memory cache for LLM-generated search summaries.

    Avoids re-summarizing identical queries within TTL.
    """

    def __init__(
        self,
        max_entries: int = 500,
        ttl_seconds: float = 3600,
    ) -> None:
        self._cache: dict[str, CachedSummary] = {}
        self._max = max_entries
        self._ttl = ttl_seconds

    @staticmethod
    def _hash(query: str) -> str:
        return hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]

    def get(self, query: str) -> CachedSummary | None:
        h = self._hash(query)
        entry = self._cache.get(h)
        if entry and time.time() < entry.expires_at:
            return entry
        if entry:
            del self._cache[h]
        return None

    def put(
        self,
        query: str,
        summary: str,
        sources: list[str],
    ) -> None:
        if len(self._cache) >= self._max:
            # Evict oldest
            oldest_key = min(
                self._cache,
                key=lambda k: self._cache[k].created_at,
            )
            del self._cache[oldest_key]

        h = self._hash(query)
        now = time.time()
        self._cache[h] = CachedSummary(
            query_hash=h,
            summary=summary,
            sources=sources,
            created_at=now,
            expires_at=now + self._ttl,
        )

    @property
    def size(self) -> int:
        return len(self._cache)


# ── #5: Multilingual Keyword Translation ───────────────────────────

# Simple bilingual keyword mappings (no ML dependency)
_TRANSLATIONS: dict[str, dict[str, str]] = {
    "ko": {
        "설치": "install",
        "사용법": "usage",
        "오류": "error",
        "설정": "configuration",
        "검색": "search",
        "파일": "file",
        "서버": "server",
        "데이터베이스": "database",
        "네트워크": "network",
        "보안": "security",
        "성능": "performance",
        "테스트": "test",
        "배포": "deploy",
        "업데이트": "update",
        "삭제": "delete",
    },
    "ja": {
        "インストール": "install",
        "エラー": "error",
        "設定": "configuration",
        "検索": "search",
        "ファイル": "file",
        "サーバー": "server",
        "データベース": "database",
        "ネットワーク": "network",
        "セキュリティ": "security",
    },
    "zh": {
        "安装": "install",
        "错误": "error",
        "配置": "configuration",
        "搜索": "search",
        "文件": "file",
        "服务器": "server",
        "数据库": "database",
        "网络": "network",
        "安全": "security",
        "性能": "performance",
    },
    "ar": {
        "تثبيت": "install",
        "خطأ": "error",
        "إعدادات": "configuration",
        "بحث": "search",
        "ملف": "file",
        "خادم": "server",
        "قاعدة بيانات": "database",
        "شبكة": "network",
        "أمان": "security",
    },
    "hi": {
        "स्थापित": "install",
        "त्रुटि": "error",
        "सेटिंग": "configuration",
        "खोज": "search",
        "फ़ाइल": "file",
        "सर्वर": "server",
        "डेटाबेस": "database",
        "नेटवर्क": "network",
        "सुरक्षा": "security",
    },
    "th": {
        "ติดตั้ง": "install",
        "ข้อผิดพลาด": "error",
        "การตั้งค่า": "configuration",
        "ค้นหา": "search",
        "ไฟล์": "file",
        "เซิร์ฟเวอร์": "server",
        "ฐานข้อมูล": "database",
        "เครือข่าย": "network",
    },
    "tr": {
        "kurulum": "install",
        "hata": "error",
        "ayarlar": "configuration",
        "arama": "search",
        "dosya": "file",
        "sunucu": "server",
        "veritabanı": "database",
        "ağ": "network",
        "güvenlik": "security",
    },
    "vi": {
        "cài đặt": "install",
        "lỗi": "error",
        "cấu hình": "configuration",
        "tìm kiếm": "search",
        "tập tin": "file",
        "máy chủ": "server",
        "cơ sở dữ liệu": "database",
        "mạng": "network",
        "bảo mật": "security",
    },
    "id": {
        "instalasi": "install",
        "kesalahan": "error",
        "pengaturan": "configuration",
        "pencarian": "search",
        "berkas": "file",
        "server": "server",
        "basis data": "database",
        "jaringan": "network",
        "keamanan": "security",
    },
}


def translate_query_keywords(
    query: str,
    source_lang: str,
) -> list[str]:
    """Translate query keywords to English for cross-lingual search.

    Returns list of English terms that can be used as additional
    search terms alongside the original query.
    """
    translations = _TRANSLATIONS.get(source_lang, {})
    if not translations:
        return []

    english_terms: list[str] = []
    for term, eng in translations.items():
        if term in query:
            english_terms.append(eng)

    return english_terms
