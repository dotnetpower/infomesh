"""CLI commands: keys export, keys rotate."""

from __future__ import annotations

import click

from infomesh.config import load_config


@click.group("keys")
def keys_group() -> None:
    """Ed25519 key management."""


@keys_group.command("export")
def keys_export() -> None:
    """Export the public key."""
    from infomesh.p2p.keys import export_public_key

    config = load_config()
    try:
        pem = export_public_key(config.node.data_dir)
        click.echo(pem)
    except FileNotFoundError as exc:
        click.echo(str(exc))


@keys_group.command("rotate")
@click.confirmation_option(prompt="This will generate a new key pair. Are you sure?")
def keys_rotate() -> None:
    """Rotate the Ed25519 key pair and publish a revocation record."""
    from infomesh.p2p.keys import rotate_keys

    config = load_config()
    try:
        old_keys, new_keys, record = rotate_keys(config.node.data_dir)
        click.echo("Old keys backed up. Revocation record saved.")
        click.echo(f"  Old Peer ID: {old_keys.peer_id}")
        click.echo(f"  New Peer ID: {new_keys.peer_id}")
        click.echo(f"  Reason: {record.reason}")
        click.echo("\nRevocation will be published to DHT on next node start.")
    except FileNotFoundError as exc:
        click.echo(str(exc))
