"""InfoMesh CLI — Click command groups and sub-commands.

This package splits the monolithic ``__main__.py`` into focused
sub-command modules, each with a single concern:

- ``serve`` — ``start``, ``stop``, ``_serve`` (background node)
- ``crawl`` — ``crawl``, ``mcp``, ``dashboard``
- ``search`` — ``search`` (local/hybrid)
- ``index`` — ``index stats``, ``index export/import``
- ``config`` — ``config show``, ``config set``
- ``keys`` — ``keys export``, ``keys rotate``
"""

from __future__ import annotations

import click
import structlog

from infomesh import __version__

# Configure structlog once at CLI entry
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


@click.group()
@click.version_option(version=__version__, prog_name="infomesh")
def cli() -> None:
    """InfoMesh — Decentralized P2P search engine for LLMs via MCP."""


# Register sub-command modules
from infomesh.cli.config import config_group  # noqa: E402
from infomesh.cli.crawl import crawl, dashboard, mcp_cmd  # noqa: E402
from infomesh.cli.index import index_group  # noqa: E402
from infomesh.cli.keys import keys_group  # noqa: E402
from infomesh.cli.peer import peer_group  # noqa: E402
from infomesh.cli.search import search  # noqa: E402
from infomesh.cli.serve import serve, start, status, stop, update  # noqa: E402

cli.add_command(start)
cli.add_command(stop)
cli.add_command(update)
cli.add_command(serve)
cli.add_command(status)
cli.add_command(crawl)
cli.add_command(mcp_cmd)
cli.add_command(dashboard)
cli.add_command(search)
cli.add_command(index_group)
cli.add_command(config_group)
cli.add_command(keys_group)
cli.add_command(peer_group)
