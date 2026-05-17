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
from infomesh.cli.crawl import crawl, dashboard, feeds_group, mcp_cmd  # noqa: E402
from infomesh.cli.index import index_group  # noqa: E402
from infomesh.cli.keys import keys_group  # noqa: E402
from infomesh.cli.peer import peer_group  # noqa: E402
from infomesh.cli.search import feedback_group, search  # noqa: E402
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
cli.add_command(feeds_group)
cli.add_command(feedback_group)


# ── #49: Doctor command ─────────────────────────────────────────


@cli.command()
def doctor() -> None:
    """Run diagnostic checks on the InfoMesh installation."""
    from infomesh.config import load_config
    from infomesh.diagnostics import run_diagnostics

    config = load_config()
    report = run_diagnostics(config.node.data_dir)

    icons = {"ok": "\u2714", "warning": "\u26a0", "error": "\u2716"}
    colors = {"ok": "green", "warning": "yellow", "error": "red"}

    click.echo("InfoMesh Doctor")
    click.echo("=" * 40)
    for check in report.checks:
        icon = icons.get(check.status, "?")
        color = colors.get(check.status)
        click.secho(f"  {icon} {check.name}: {check.message}", fg=color)
    click.echo()
    click.secho(
        f"Summary: {report.summary}",
        fg="green" if report.ok else "yellow",
        bold=True,
    )


# ── #45: Benchmark command ──────────────────────────────────────


@cli.command()
@click.option("--iterations", "-n", default=50, help="Iterations per benchmark")
def bench(iterations: int) -> None:
    """Run performance benchmarks."""
    from infomesh.benchmarks import BenchmarkSuite, benchmark
    from infomesh.search.cjk import is_cjk_text, tokenize_query_cjk
    from infomesh.search.nlp import expand_query, parse_natural_query
    from infomesh.search.passage import split_passages
    from infomesh.search.quality import QueryIntentClassifier

    suite = BenchmarkSuite()

    suite.add(
        benchmark(
            expand_query,
            "python async error",
            iterations=iterations,
            name="query_expansion",
        )
    )
    suite.add(
        benchmark(
            parse_natural_query,
            "python tutorial last week site:docs.python.org",
            iterations=iterations,
            name="nlp_parse",
        )
    )
    suite.add(
        benchmark(
            split_passages,
            "Hello world. " * 100,
            iterations=iterations,
            name="passage_split",
        )
    )
    suite.add(
        benchmark(
            is_cjk_text,
            "\u4e2d\u6587\u6d4b\u8bd5\u6587\u672c",
            iterations=iterations,
            name="cjk_detect",
        )
    )
    suite.add(
        benchmark(
            tokenize_query_cjk,
            "\u4e2d\u6587\u641c\u7d22\u6d4b\u8bd5",
            iterations=iterations,
            name="cjk_tokenize",
        )
    )
    suite.add(
        benchmark(
            QueryIntentClassifier().classify,
            "how to install python",
            iterations=iterations,
            name="intent_classify",
        )
    )

    click.echo(suite.report())
