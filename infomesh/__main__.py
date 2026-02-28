"""InfoMesh CLI entry point.

Delegates to ``infomesh.cli`` which houses all Click commands.
Kept minimal so that ``python -m infomesh`` and the ``infomesh``
console-script entry point both resolve here.
"""

from __future__ import annotations

from infomesh.cli import cli


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
