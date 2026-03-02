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
import shutil
import subprocess

import structlog

from infomesh.config import Config

logger = structlog.get_logger()

# Simple email validation pattern
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def is_git_installed() -> bool:
    """Check if git is available on the system PATH."""
    return shutil.which("git") is not None


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


def run_first_start_checks(config: Config, interactive: bool = True) -> str | None:
    """Run first-start checks: git installation and GitHub account linking.

    This function is called during ``infomesh start`` to guide new users
    through connecting their GitHub identity for cross-node credit
    aggregation.

    Args:
        config: Current configuration.
        interactive: If ``True`` and running in a TTY, prompt the user.

    Returns:
        Resolved GitHub email (may be newly set), or ``None``.
    """
    import sys

    import click

    from infomesh.config import save_config

    is_tty = interactive and sys.stdin.isatty()

    # ── Step 1: Check git installation ────────────────────────
    git_available = is_git_installed()
    if not git_available:
        click.echo(
            click.style("  ⚠ git not found", fg="yellow")
            + " — install git to auto-detect your GitHub identity."
        )
        click.echo("    Install: https://git-scm.com/downloads")
        click.echo(
            '    Or set manually: infomesh config set node.github_email "you@email.com"'
        )
        logger.info("git_not_installed")

        # Even without git, user can set email manually in interactive mode
        if is_tty:
            email_input: str = str(
                click.prompt(
                    "  Enter GitHub email (or press Enter to skip)",
                    default="",
                    show_default=False,
                )
            )
            if email_input and is_valid_email(email_input):
                from dataclasses import replace as dc_replace

                new_config = dc_replace(
                    config,
                    node=dc_replace(config.node, github_email=email_input),
                )
                save_config(new_config)
                click.echo(click.style("  ✔ GitHub linked: ", fg="green") + email_input)
                return email_input
        return None

    # ── Step 2: Check for existing GitHub identity ────────────
    # Already configured in config.toml
    if config.node.github_email:
        return config.node.github_email

    # Auto-detect from git
    detected = detect_git_email()
    if detected:
        click.echo(
            click.style("  ✔ GitHub detected: ", fg="green")
            + detected
            + " (from git config)"
        )
        return detected

    # ── Step 3: Interactive prompt ────────────────────────────
    click.echo(
        click.style("  ⚠ GitHub not linked", fg="yellow")
        + " — credits will be local-only."
    )

    if not is_tty:
        click.echo('    To link: infomesh config set node.github_email "you@email.com"')
        return None

    if click.confirm(
        "  Link your GitHub account now? (enables cross-node credits)",
        default=True,
    ):
        email_input = str(
            click.prompt(
                "  GitHub email",
                default="",
                show_default=False,
            )
        )
        if email_input and is_valid_email(email_input):
            from dataclasses import replace as dc_replace

            new_config = dc_replace(
                config,
                node=dc_replace(config.node, github_email=email_input),
            )
            save_config(new_config)
            click.echo(click.style("  ✔ GitHub linked: ", fg="green") + email_input)
            click.echo("    Credits will aggregate across all nodes using this email.")
            return email_input
        elif email_input:
            click.echo(
                click.style("  ✗ Invalid email format", fg="red")
                + " — skipping. Set later with:"
            )
            click.echo('    infomesh config set node.github_email "you@email.com"')
    else:
        click.echo(
            "    Skipped. Set later with:"
            ' infomesh config set node.github_email "you@email.com"'
        )

    return None
