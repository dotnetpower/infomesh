"""GitHub identity resolution for cross-node credit aggregation.

When a GitHub email is associated with a node, credits earned on
multiple nodes running under the same GitHub account are logically
linked.  This enables cross-node credit aggregation so that
contribution scores accumulate across all machines a user operates.

The identity is determined in the following priority order:
1. Explicit ``github_email`` in ``~/.infomesh/config.toml``
2. ``git config --global user.email`` (auto-detected)
3. None — credits are local-only to this node

Peering is always by Ed25519 peer ID, so nodes with the same GitHub
account can still peer freely.
"""

from __future__ import annotations

import re
import subprocess

import structlog

from infomesh.config import Config

logger = structlog.get_logger()

# Simple email validation pattern
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def detect_git_email() -> str | None:
    """Auto-detect the global git user email.

    Returns:
        The email string, or ``None`` if git is not installed or
        no global email is configured.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--global", "user.email"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            email = result.stdout.strip()
            if email and is_valid_email(email):
                return email
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def is_valid_email(email: str) -> bool:
    """Check if a string looks like a valid email address."""
    return bool(_EMAIL_RE.match(email))


def resolve_github_email(config: Config) -> str | None:
    """Resolve the GitHub email for this node.

    Priority:
    1. ``config.node.github_email`` (explicit config)
    2. ``git config --global user.email`` (auto-detected)

    Returns:
        The resolved email or ``None``.
    """
    # 1. Explicit config
    if config.node.github_email:
        logger.info(
            "github_identity_configured",
            email=config.node.github_email,
        )
        return config.node.github_email

    # 2. Auto-detect from git
    detected = detect_git_email()
    if detected:
        logger.info(
            "github_identity_detected",
            email=detected,
            source="git config --global user.email",
        )
        return detected

    logger.info("github_identity_not_found")
    return None


def format_startup_message(email: str | None) -> str:
    """Build a startup guidance message about GitHub identity.

    Args:
        email: Resolved GitHub email, or ``None`` if not connected.

    Returns:
        Multi-line string for CLI output.
    """
    if email:
        return (
            f"  GitHub:  {email}\n"
            f"           Credits are linked to this account across all nodes."
        )

    return (
        "  GitHub:  not connected\n"
        "           Credits are tracked locally on this node only.\n"
        "           Connect your GitHub account to aggregate credits\n"
        "           across all your nodes and search for free forever.\n"
        "\n"
        "           To connect:\n"
        '             infomesh config set node.github_email "your@email.com"\n'
        "           Or set git globally:\n"
        '             git config --global user.email "your@email.com"'
    )
