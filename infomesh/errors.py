"""Structured error codes and error handling.

Feature #58: Structured error codes with resolution links.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    """Error category classification."""

    AUTH = "AUTH"
    SEARCH = "SEARCH"
    CRAWL = "CRAWL"
    INDEX = "INDEX"
    NETWORK = "NETWORK"
    CONFIG = "CONFIG"
    RESOURCE = "RESOURCE"
    SECURITY = "SECURITY"


@dataclass(frozen=True)
class InfoMeshError:
    """Structured error with code, message, and resolution."""

    code: str
    category: ErrorCategory
    message: str
    resolution: str
    http_status: int = 400

    def to_dict(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "category": self.category.value,
                "message": self.message,
                "resolution": self.resolution,
            },
        }

    def format(self) -> str:
        return f"Error [{self.code}]: {self.message}\nResolution: {self.resolution}"


# ── Pre-defined error catalog ─────────────────────────────────────

ERRORS: dict[str, InfoMeshError] = {
    "E001": InfoMeshError(
        code="INFOMESH_E001",
        category=ErrorCategory.AUTH,
        message="Invalid or missing API key",
        resolution=("Set INFOMESH_API_KEY env var or pass api_key parameter"),
        http_status=401,
    ),
    "E002": InfoMeshError(
        code="INFOMESH_E002",
        category=ErrorCategory.AUTH,
        message="Insufficient role permissions",
        resolution=("Contact admin to assign appropriate role (admin/reader/crawler)"),
        http_status=403,
    ),
    "E003": InfoMeshError(
        code="INFOMESH_E003",
        category=ErrorCategory.SEARCH,
        message="Query must be a non-empty string",
        resolution="Provide a non-empty query parameter",
    ),
    "E004": InfoMeshError(
        code="INFOMESH_E004",
        category=ErrorCategory.SEARCH,
        message="Query exceeds maximum length (1000 chars)",
        resolution="Shorten query to under 1000 characters",
    ),
    "E005": InfoMeshError(
        code="INFOMESH_E005",
        category=ErrorCategory.CRAWL,
        message="URL blocked for security reasons (SSRF)",
        resolution=("Only http:// and https:// public URLs are allowed"),
    ),
    "E006": InfoMeshError(
        code="INFOMESH_E006",
        category=ErrorCategory.CRAWL,
        message="Crawl rate limit exceeded (60 URLs/hour)",
        resolution=(
            "Wait before submitting more URLs or increase rate limit in config"
        ),
        http_status=429,
    ),
    "E007": InfoMeshError(
        code="INFOMESH_E007",
        category=ErrorCategory.CRAWL,
        message="robots.txt disallows crawling this URL",
        resolution=(
            "This URL is blocked by the site's robots.txt. Choose a different URL."
        ),
    ),
    "E008": InfoMeshError(
        code="INFOMESH_E008",
        category=ErrorCategory.INDEX,
        message="Document exceeds maximum size",
        resolution=("Reduce document size or increase max_doc_size_kb in config"),
    ),
    "E009": InfoMeshError(
        code="INFOMESH_E009",
        category=ErrorCategory.NETWORK,
        message="No P2P peers connected",
        resolution=("Check network connectivity and bootstrap node configuration"),
    ),
    "E010": InfoMeshError(
        code="INFOMESH_E010",
        category=ErrorCategory.RESOURCE,
        message="Insufficient disk space",
        resolution=("Free up disk space (minimum 500MB required)"),
    ),
    "E011": InfoMeshError(
        code="INFOMESH_E011",
        category=ErrorCategory.CONFIG,
        message="Invalid configuration value",
        resolution=(
            "Check config.toml for valid values. Run 'infomesh config show' to review."
        ),
    ),
    "E012": InfoMeshError(
        code="INFOMESH_E012",
        category=ErrorCategory.SEARCH,
        message="Batch search exceeds maximum queries (10)",
        resolution="Reduce batch size to 10 or fewer queries",
    ),
    "E013": InfoMeshError(
        code="INFOMESH_E013",
        category=ErrorCategory.SECURITY,
        message="IP address blocked by access control",
        resolution="Contact admin to add IP to allowlist",
        http_status=403,
    ),
    "E014": InfoMeshError(
        code="INFOMESH_E014",
        category=ErrorCategory.AUTH,
        message="JWT token expired or invalid",
        resolution="Obtain a new JWT token and retry",
        http_status=401,
    ),
    "E015": InfoMeshError(
        code="INFOMESH_E015",
        category=ErrorCategory.CRAWL,
        message="Crawler worker not available",
        resolution=("Start the node with crawl capability or use role=full"),
    ),
    "E016": InfoMeshError(
        code="INFOMESH_E016",
        category=ErrorCategory.SEARCH,
        message="Vector search not available",
        resolution=("Install vector dependencies: uv sync --extra vector"),
    ),
    "E017": InfoMeshError(
        code="INFOMESH_E017",
        category=ErrorCategory.RESOURCE,
        message="Node in defensive mode (overloaded)",
        resolution=("Reduce load or wait for resource governor to recover"),
    ),
    "E018": InfoMeshError(
        code="INFOMESH_E018",
        category=ErrorCategory.NETWORK,
        message="Port already in use",
        resolution=("Stop the existing process or use a different port"),
    ),
    "E019": InfoMeshError(
        code="INFOMESH_E019",
        category=ErrorCategory.CRAWL,
        message="Paywall detected",
        resolution=("This content is behind a paywall and cannot be retrieved"),
    ),
    "E020": InfoMeshError(
        code="INFOMESH_E020",
        category=ErrorCategory.AUTH,
        message="Rate limit exceeded for API key",
        resolution=("Wait for rate limit window to reset or contact admin"),
        http_status=429,
    ),
}


def get_error(code: str) -> InfoMeshError | None:
    """Look up an error by short code (e.g. 'E001')."""
    return ERRORS.get(code)


def format_error(code: str) -> str:
    """Format an error message by code."""
    err = ERRORS.get(code)
    if err is None:
        return f"Unknown error: {code}"
    return err.format()
