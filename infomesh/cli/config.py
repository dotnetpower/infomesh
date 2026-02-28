"""CLI commands: config show, config set."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import click

from infomesh.config import load_config


@click.group("config")
def config_group() -> None:
    """Configuration management."""


@config_group.command("show")
def config_show() -> None:
    """Show current configuration."""
    config = load_config()
    cfg = asdict(config)
    for section_name, section in cfg.items():
        click.echo(f"[{section_name}]")
        if isinstance(section, dict):
            for key, value in section.items():
                click.echo(f"  {key} = {value}")
        else:
            click.echo(f"  {section}")
        click.echo()


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a configuration value (section.key = value).

    Example: infomesh config set crawl.max_concurrent 10
    """
    if "." not in key:
        click.echo(
            "Error: Key must be in 'section.key' format (e.g., crawl.max_concurrent)"
        )
        return

    section, field_name = key.split(".", 1)
    config = load_config()
    config_path = config.node.data_dir / "config.toml"

    # Load existing TOML or create empty
    raw: dict[str, dict[str, object]] = {}
    if config_path.exists():
        import tomllib

        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    if section not in raw:
        raw[section] = {}

    # Coerce value to appropriate type
    raw[section][field_name] = _coerce_cli_value(value)

    # Write back as TOML
    _write_toml(config_path, raw)
    click.echo(f"Set {section}.{field_name} = {raw[section][field_name]}")


def _coerce_cli_value(value: str) -> object:
    """Coerce a CLI string to bool, int, float, or str."""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _write_toml(path: Path | str, data: dict) -> None:
    """Write a flat TOML dict to *path*.

    Uses a simple serializer sufficient for InfoMesh's config structure.
    """
    lines: list[str] = []
    for sec, fields in data.items():
        lines.append(f"[{sec}]")
        if isinstance(fields, dict):
            for k, v in fields.items():
                if isinstance(v, str):
                    lines.append(f'{k} = "{v}"')
                elif isinstance(v, bool):
                    lines.append(f"{k} = {'true' if v else 'false'}")
                else:
                    lines.append(f"{k} = {v}")
        lines.append("")

    p = Path(path) if not isinstance(path, Path) else path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines))
