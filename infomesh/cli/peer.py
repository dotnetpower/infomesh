"""CLI commands: peer add/list/remove — manual P2P peer management."""

from __future__ import annotations

import json

import click

from infomesh.config import load_config


@click.group(name="peer")
def peer_group() -> None:
    """Manage P2P peers (add, list, remove bootstrap nodes)."""


@peer_group.command(name="list")
def list_peers() -> None:
    """Show connected peers and bootstrap nodes."""
    config = load_config()

    # Bootstrap nodes from config
    bs_nodes = config.network.bootstrap_nodes
    click.echo("Bootstrap nodes:")
    if bs_nodes:
        for addr in bs_nodes:
            click.echo(f"  {addr}")
    else:
        click.echo("  (none configured)")
        click.echo("  Add with: infomesh peer add /ip4/<IP>/tcp/4001/p2p/<PEER_ID>")

    # Connected peers from p2p_status.json
    click.echo()
    status_path = config.node.data_dir / "p2p_status.json"
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text())
            state = data.get("state", "stopped")
            peers = data.get("peer_ids", [])
            bs = data.get("bootstrap", {})
            click.echo(f"P2P state: {state}")
            click.echo(f"Connected peers: {len(peers)}")
            for pid in peers:
                click.echo(f"  {pid}")

            if isinstance(bs, dict) and bs:
                bs_conn = bs.get("connected", 0)
                bs_fail = bs.get("failed", 0)
                click.echo(f"Bootstrap: {bs_conn} connected, {bs_fail} failed")
                failed = bs.get("failed_addrs", [])
                if isinstance(failed, list):
                    for fa in failed:
                        click.echo("  " + click.style(f"✗ {fa}", fg="red"))
        except (json.JSONDecodeError, OSError):
            click.echo("P2P state: unknown (status file unreadable)")
    else:
        click.echo("P2P state: not started")


@peer_group.command()
@click.argument("multiaddr")
def add(multiaddr: str) -> None:
    """Add a bootstrap node to config.

    MULTIADDR: libp2p multiaddr, e.g.
    /ip4/1.2.3.4/tcp/4001/p2p/12D3KooW...
    """
    # Validate format
    if not multiaddr.startswith("/ip4/") and not multiaddr.startswith("/ip6/"):
        click.echo(click.style("Error: ", fg="red") + "Invalid multiaddr format.")
        click.echo("Expected: /ip4/<IP>/tcp/<PORT>/p2p/<PEER_ID>")
        return

    if "/p2p/" not in multiaddr:
        click.echo(
            click.style("Warning: ", fg="yellow") + "No /p2p/<PEER_ID> in address."
            " Connection may fail without peer ID."
        )

    config = load_config()
    current = list(config.network.bootstrap_nodes)

    if multiaddr in current:
        click.echo("Already in bootstrap list.")
        return

    current.append(multiaddr)

    # Update config
    from dataclasses import replace as dc_replace

    from infomesh.config import save_config

    new_net = dc_replace(config.network, bootstrap_nodes=current)
    new_config = dc_replace(config, network=new_net)
    save_config(new_config)

    click.echo(click.style("Added: ", fg="green") + multiaddr)
    click.echo(
        "Restart the node for changes to take effect: infomesh stop && infomesh start"
    )

    # Quick connectivity test
    import socket

    parts = multiaddr.split("/")
    try:
        ip_idx = parts.index("ip4") + 1
        tcp_idx = parts.index("tcp") + 1
        ip = parts[ip_idx]
        port = int(parts[tcp_idx])

        click.echo(f"Testing TCP {ip}:{port}... ", nl=False)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((ip, port))
        sock.close()
        if result == 0:
            click.echo(click.style("reachable ✓", fg="green"))
        else:
            click.echo(click.style("unreachable ✗", fg="red"))
            click.echo(
                "  Ensure the bootstrap node is running"
                " and port is open (firewall/NSG)."
            )
    except (ValueError, IndexError):
        click.echo("(skipped — could not parse IP/port)")


@peer_group.command()
@click.argument("multiaddr")
def remove(multiaddr: str) -> None:
    """Remove a bootstrap node from config.

    MULTIADDR: The exact multiaddr string to remove.
    """
    config = load_config()
    current = list(config.network.bootstrap_nodes)

    if multiaddr not in current:
        click.echo("Not found in bootstrap list.")
        click.echo("Current nodes:")
        for addr in current:
            click.echo(f"  {addr}")
        return

    current.remove(multiaddr)

    from dataclasses import replace as dc_replace

    from infomesh.config import save_config

    new_net = dc_replace(config.network, bootstrap_nodes=current)
    new_config = dc_replace(config, network=new_net)
    save_config(new_config)

    click.echo(click.style("Removed: ", fg="green") + multiaddr)


@peer_group.command()
def test() -> None:
    """Test connectivity to all bootstrap nodes."""
    import socket

    config = load_config()
    bs_nodes = config.network.bootstrap_nodes

    if not bs_nodes:
        click.echo("No bootstrap nodes configured.")
        click.echo("Add with: infomesh peer add /ip4/<IP>/tcp/4001/p2p/<PEER_ID>")
        return

    click.echo(f"Testing {len(bs_nodes)} bootstrap node(s)...\n")

    ok = 0
    for addr in bs_nodes:
        parts = addr.split("/")
        try:
            ip_idx = parts.index("ip4") + 1
            tcp_idx = parts.index("tcp") + 1
            ip = parts[ip_idx]
            port = int(parts[tcp_idx])

            click.echo(f"  {addr}")
            click.echo(f"    TCP {ip}:{port} ... ", nl=False)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((ip, port))
            sock.close()
            if result == 0:
                click.echo(click.style("OK ✓", fg="green"))
                ok += 1
            else:
                click.echo(click.style("FAIL ✗", fg="red"))
                click.echo("    → Node not running or port blocked by firewall/NSG")
        except (ValueError, IndexError):
            click.echo(
                click.style("    SKIP", fg="yellow") + " — could not parse address"
            )

    click.echo(f"\nResult: {ok}/{len(bs_nodes)} reachable")
    if ok == 0:
        click.echo("\nNo bootstrap nodes reachable. Peers cannot be discovered.")
        click.echo("Troubleshooting:")
        click.echo("  1. Is the bootstrap node running?")
        click.echo("  2. Is TCP port 4001 open in firewall / Azure NSG / AWS SG?")
        click.echo("  3. Is the IP address correct?")
        click.echo("  4. Try: nc -zv <IP> 4001 (or Test-NetConnection on Windows)")
