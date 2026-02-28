"""Settings pane — dashboard/node configuration editor."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from dataclasses import dataclass, fields
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Collapsible,
    Input,
    Label,
    Select,
    Static,
    Switch,
)

from infomesh.config import (
    _ALLOWED_VALUES,
    _VALUE_CONSTRAINTS,
    Config,
    CrawlConfig,
    DashboardConfig,
    NetworkConfig,
    ResourceConfig,
    StorageConfig,
    save_config,
)

# ── Section descriptors ───────────────────────────────────────────


@dataclass
class _FieldSpec:
    """Describes a single configurable field."""

    section: str  # Config section (e.g. "crawl")
    key: str  # Field name in the dataclass
    label: str  # Human-readable label
    widget_id: str  # Unique widget ID
    kind: str  # "int" | "float" | "bool" | "select" | "str"
    default: object  # Default value
    hint: str = ""  # Short description


def _specs_for_section(
    section_name: str,
    cls: type,
    default_obj: object,
    field_labels: dict[str, tuple[str, str]],
) -> list[_FieldSpec]:
    """Build FieldSpecs for a dataclass section."""
    specs: list[_FieldSpec] = []
    for f in fields(cls):
        if f.name not in field_labels:
            continue
        label, hint = field_labels[f.name]
        default_val = getattr(default_obj, f.name)

        if f.name in _ALLOWED_VALUES:
            kind = "select"
        elif isinstance(default_val, bool):
            kind = "bool"
        elif isinstance(default_val, int):
            kind = "int"
        elif isinstance(default_val, float):
            kind = "float"
        else:
            kind = "str"

        specs.append(
            _FieldSpec(
                section=section_name,
                key=f.name,
                label=label,
                widget_id=f"settings-{section_name}-{f.name}",
                kind=kind,
                default=default_val,
                hint=hint,
            )
        )
    return specs


_DEFAULTS = Config()

_CRAWL_FIELDS: dict[str, tuple[str, str]] = {
    "max_concurrent": ("Max Concurrent", "Simultaneous crawl workers (1–100)"),
    "politeness_delay": ("Politeness Delay", "Seconds between same-domain requests"),
    "max_depth": ("Max Depth", "Maximum link-follow depth (0–10)"),
    "urls_per_hour": ("URLs / Hour", "Rate limit per hour (1–10 000)"),
}

_RESOURCE_FIELDS: dict[str, tuple[str, str]] = {
    "profile": ("Profile", "Resource profile preset"),
    "cpu_cores_limit": ("CPU Cores Limit", "Max CPU cores to use (1–256)"),
    "memory_limit_mb": ("Memory Limit (MB)", "Max memory in MB (64–1 048 576)"),
    "cpu_nice": ("CPU Nice", "Process priority niceness (0–19)"),
    "disk_io_priority": ("Disk I/O Priority", "I/O scheduling class"),
}

_NETWORK_FIELDS: dict[str, tuple[str, str]] = {
    "upload_limit_mbps": ("Upload Limit (Mbps)", "Max upload bandwidth"),
    "download_limit_mbps": ("Download Limit (Mbps)", "Max download bandwidth"),
    "replication_factor": ("Replication Factor", "DHT replication count (1–10)"),
}

_DASHBOARD_FIELDS: dict[str, tuple[str, str]] = {
    "bgm_auto_start": ("BGM Auto Start", "Play music when dashboard opens"),
    "bgm_volume": ("BGM Volume", "Volume (0–100 %)"),
    "refresh_interval": (
        "Refresh Interval",
        "Dashboard data refresh period (0.2–5.0 s)",
    ),
    "theme": ("Theme", "Dashboard colour theme"),
}

_STORAGE_FIELDS: dict[str, tuple[str, str]] = {
    "compression_enabled": ("Compression", "Enable zstd compression"),
    "compression_level": ("Compression Level", "zstd level (1–22)"),
    "max_cache_size_mb": ("Cache Size (MB)", "Max crawl cache (10–100 000 MB)"),
    "max_index_size_gb": ("Index Size (GB)", "Max index size (1–10 000 GB)"),
    "cache_ttl_days": ("Cache TTL (days)", "Cached page lifetime (1–365)"),
}


def _all_specs() -> list[tuple[str, str, list[_FieldSpec]]]:
    """Return (section_title, collapse_id, specs) for each section."""
    return [
        (
            "🕷️  Crawler",
            "sec-crawl",
            _specs_for_section(
                "crawl",
                CrawlConfig,
                _DEFAULTS.crawl,
                _CRAWL_FIELDS,
            ),
        ),
        (
            "🖥️  Resources",
            "sec-resources",
            _specs_for_section(
                "resources",
                ResourceConfig,
                _DEFAULTS.resources,
                _RESOURCE_FIELDS,
            ),
        ),
        (
            "🌐  Network",
            "sec-network",
            _specs_for_section(
                "network",
                NetworkConfig,
                _DEFAULTS.network,
                _NETWORK_FIELDS,
            ),
        ),
        (
            "🎨  Dashboard",
            "sec-dashboard",
            _specs_for_section(
                "dashboard",
                DashboardConfig,
                _DEFAULTS.dashboard,
                _DASHBOARD_FIELDS,
            ),
        ),
        (
            "💾  Storage",
            "sec-storage",
            _specs_for_section(
                "storage",
                StorageConfig,
                _DEFAULTS.storage,
                _STORAGE_FIELDS,
            ),
        ),
    ]


# ── Settings that require a process restart ───────────────────
# Changes to these keys cannot be applied at runtime and need
# a full node restart to take effect.

_RESTART_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        # Network
        "upload_limit_mbps",
        "download_limit_mbps",
        "replication_factor",
        # Storage
        "compression_enabled",
        "compression_level",
        "max_cache_size_mb",
        "max_index_size_gb",
        "cache_ttl_days",
    }
)


# ── Restart confirmation modal ────────────────────────────────


class RestartConfirmScreen(ModalScreen[bool]):
    """Modal asking the user whether to restart the node process."""

    DEFAULT_CSS = """
    RestartConfirmScreen {
        align: center middle;
    }

    #restart-dialog {
        width: 60;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #restart-dialog Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }

    #restart-buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }

    #restart-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, changed_keys: list[str], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._changed_keys = changed_keys

    def compose(self) -> ComposeResult:
        key_list = ", ".join(self._changed_keys)
        with Vertical(id="restart-dialog"):
            yield Label("[bold]Restart Required[/bold]")
            yield Label(
                f"The following settings need a restart to apply:\n"
                f"[yellow]{key_list}[/yellow]"
            )
            yield Label("Restart the node now?")
            with Horizontal(id="restart-buttons"):
                yield Button(
                    "Restart Now",
                    variant="error",
                    id="btn-restart-yes",
                )
                yield Button("Later", id="btn-restart-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-restart-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)


# ── Pane widget ───────────────────────────────────────────────────


class SettingsPane(Widget):
    """Full-screen settings editor with collapsible sections."""

    DEFAULT_CSS = """
    SettingsPane {
        height: 1fr;
    }

    .settings-section {
        margin-bottom: 1;
    }

    .settings-row {
        height: auto;
        margin: 0 0 0 2;
        padding: 0;
    }

    .settings-label {
        width: 24;
        text-style: bold;
        padding: 0 1 0 0;
    }

    .settings-hint {
        width: 1fr;
        color: $text-muted;
    }

    .settings-input {
        width: 20;
    }

    .settings-switch {
        width: auto;
    }

    .settings-select {
        width: 28;
    }

    #settings-buttons {
        height: auto;
        margin-top: 1;
        padding: 0 2;
    }

    #settings-buttons Button {
        margin-right: 2;
    }

    #settings-status {
        margin-top: 1;
        padding: 0 2;
        height: auto;
        color: $success;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._sections = _all_specs()

    # ── compose ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static("[bold]⚙  Settings[/bold]", classes="panel-title")
        with VerticalScroll():
            for title, collapse_id, spec_list in self._sections:
                with Collapsible(title=title, id=collapse_id, collapsed=False):
                    for spec in spec_list:
                        yield from self._field_row(spec)

            # Buttons
            with Horizontal(id="settings-buttons"):
                yield Button("💾  Save", variant="success", id="btn-save")
                yield Button("↺  Reset to Defaults", variant="warning", id="btn-reset")
            yield Static("", id="settings-status")

    def _field_row(self, spec: _FieldSpec) -> ComposeResult:
        """Yield a label + widget row for one field."""
        with Horizontal(classes="settings-row"):
            yield Label(f"{spec.label}:", classes="settings-label")
            yield from self._field_widget(spec)
            yield Label(spec.hint, classes="settings-hint")

    def _field_widget(self, spec: _FieldSpec) -> ComposeResult:
        """Yield the appropriate input widget."""
        current_val = self._current_value(spec)

        if spec.kind == "bool":
            yield Switch(
                value=bool(current_val),
                id=spec.widget_id,
                classes="settings-switch",
            )
        elif spec.kind == "select":
            allowed = sorted(_ALLOWED_VALUES.get(spec.key, set()))
            options = [(v, v) for v in allowed]
            yield Select(
                options,
                value=str(current_val),
                id=spec.widget_id,
                classes="settings-select",
            )
        else:
            # int / float / str → text input
            constraint = _VALUE_CONSTRAINTS.get(spec.key)
            placeholder = f"{constraint[0]}–{constraint[1]}" if constraint else ""
            yield Input(
                value=str(current_val),
                placeholder=placeholder,
                id=spec.widget_id,
                classes="settings-input",
            )

    # ── helpers ───────────────────────────────────────────────

    def _current_value(self, spec: _FieldSpec) -> object:
        """Read the current config value for a spec."""
        section_obj = getattr(self._config, spec.section, None)
        if section_obj is None:
            return spec.default
        return getattr(section_obj, spec.key, spec.default)

    def _collect_values(self) -> dict[str, dict[str, object]]:
        """Collect values from all widgets, grouped by section."""
        result: dict[str, dict[str, object]] = {}

        for _title, _cid, spec_list in self._sections:
            for spec in spec_list:
                try:
                    widget = self.query_one(f"#{spec.widget_id}")
                except Exception:  # noqa: BLE001
                    continue

                if spec.kind == "bool":
                    val = widget.value  # type: ignore[union-attr]
                elif spec.kind == "select":
                    raw = widget.value  # type: ignore[union-attr]
                    val = str(raw) if raw is not Select.BLANK else spec.default
                elif spec.kind == "int":
                    try:
                        val = int(widget.value)  # type: ignore[union-attr]
                    except (ValueError, TypeError):
                        val = spec.default
                elif spec.kind == "float":
                    try:
                        val = float(widget.value)  # type: ignore[union-attr]
                    except (ValueError, TypeError):
                        val = spec.default
                else:
                    val = widget.value  # type: ignore[union-attr]

                result.setdefault(spec.section, {})[spec.key] = val

        return result

    def _rebuild_config(self, values: dict[str, dict[str, object]]) -> Config:
        """Build a new Config from collected widget values."""
        from dataclasses import asdict

        section_map = {
            "node": self._config.node,
            "crawl": self._config.crawl,
            "network": self._config.network,
            "index": self._config.index,
            "llm": self._config.llm,
            "storage": self._config.storage,
            "resources": self._config.resources,
            "dashboard": self._config.dashboard,
        }

        kwargs: dict[str, object] = {}
        for section_name, current_obj in section_map.items():
            cls = type(current_obj)
            base = dict(asdict(current_obj))
            if section_name in values:
                base.update(values[section_name])
            # Convert Path fields back
            for f in fields(cls):
                if f.name in base and isinstance(getattr(current_obj, f.name), Path):
                    base[f.name] = Path(str(base[f.name]))
            kwargs[section_name] = cls(**base)

        return Config(**kwargs)  # type: ignore[arg-type]

    def _load_values_from_config(self, config: Config) -> None:
        """Push config values back into the widgets."""
        for _title, _cid, spec_list in self._sections:
            for spec in spec_list:
                section_obj = getattr(config, spec.section, None)
                if section_obj is None:
                    continue
                val = getattr(section_obj, spec.key, spec.default)
                try:
                    widget = self.query_one(f"#{spec.widget_id}")
                except Exception:  # noqa: BLE001
                    continue

                if spec.kind == "bool":
                    widget.value = bool(val)  # type: ignore[union-attr]
                elif spec.kind == "select":
                    widget.value = str(val)  # type: ignore[union-attr]
                else:
                    widget.value = str(val)  # type: ignore[union-attr]

    # ── event handlers ────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self._do_save()
        elif event.button.id == "btn-reset":
            self._do_reset()

    def _do_save(self) -> None:
        """Persist current widget values to config.toml.

        If only hot-reloadable settings changed, apply immediately.
        If restart-required settings changed, ask the user for confirmation.
        """
        status = self.query_one("#settings-status", Static)
        try:
            values = self._collect_values()
            new_config = self._rebuild_config(values)
            save_config(new_config)

            # Detect which keys changed vs old config
            restart_keys = self._detect_restart_keys(self._config, new_config)

            self._config = new_config
            # Push hot-reloadable settings into the running app
            self._apply_hot_reload(new_config)

            if restart_keys:
                # Some settings require restart — ask the user
                status.update(
                    "[bold green]✓ Saved.[/bold green] "
                    "[yellow]Some changes need a restart.[/yellow]"
                )
                self.app.push_screen(
                    RestartConfirmScreen(restart_keys),
                    self._handle_restart_response,
                )
            else:
                status.update("[bold green]✓ Settings saved and applied[/bold green]")
                self.notify("Settings saved and applied", title="Settings", timeout=3)
        except Exception as exc:  # noqa: BLE001
            status.update(f"[bold red]✗ Save failed: {exc}[/bold red]")
            self.notify(f"Save failed: {exc}", title="Settings", severity="error")

    def _detect_restart_keys(
        self,
        old: Config,
        new: Config,
    ) -> list[str]:
        """Return user-facing labels of changed settings that require restart."""
        changed: list[str] = []
        for _title, _cid, spec_list in self._sections:
            for spec in spec_list:
                if spec.key not in _RESTART_REQUIRED_KEYS:
                    continue
                old_val = getattr(
                    getattr(old, spec.section, None),
                    spec.key,
                    spec.default,
                )
                new_val = getattr(
                    getattr(new, spec.section, None),
                    spec.key,
                    spec.default,
                )
                if old_val != new_val:
                    changed.append(spec.label)
        return changed

    def _apply_hot_reload(self, new_config: Config) -> None:
        """Push hot-reloadable settings into the running app."""
        try:
            app = self.app
            # Update the app-level config reference
            app.config = new_config  # type: ignore[attr-defined]

            # Apply theme change immediately
            if hasattr(app, "theme"):
                app.theme = new_config.dashboard.theme  # type: ignore[assignment]

            # Update dashboard data cache refresh interval
            if hasattr(app, "_data_cache"):
                app._data_cache._ttl = new_config.dashboard.refresh_interval  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _handle_restart_response(self, restart: bool) -> None:
        """Callback from RestartConfirmScreen."""
        status = self.query_one("#settings-status", Static)
        if restart:
            status.update("[bold yellow]⟳ Restarting node…[/bold yellow]")
            self.notify("Restarting node…", title="Settings", timeout=2)
            self._restart_node()
        else:
            status.update(
                "[bold green]✓ Saved.[/bold green] "
                "[dim]Restart later to apply all changes.[/dim]"
            )
            self.notify(
                "Saved — restart later to apply all changes",
                title="Settings",
                timeout=4,
            )

    def _restart_node(self) -> None:
        """Restart the InfoMesh node process."""
        import subprocess

        app = self.app
        node_pid = getattr(app, "_node_pid", None)

        # Stop the current node
        if node_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(node_pid, signal.SIGTERM)

        # Re-launch the serve process
        serve_cmd = [sys.executable, "-m", "infomesh", "_serve"]
        log_path = self._config.node.data_dir / "node.log"
        try:
            log_file = open(log_path, "a")  # noqa: SIM115
            proc = subprocess.Popen(
                serve_cmd,
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            log_file.close()
            # Update app's node_pid so quit dialog still works
            if hasattr(app, "_node_pid"):
                app._node_pid = proc.pid  # type: ignore[attr-defined]
            self.notify(
                f"Node restarted (PID {proc.pid})",
                title="Settings",
                timeout=3,
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(
                f"Restart failed: {exc}",
                title="Settings",
                severity="error",
            )

    def _do_reset(self) -> None:
        """Reset all widgets to default values."""
        status = self.query_one("#settings-status", Static)
        defaults = Config()
        self._load_values_from_config(defaults)
        status.update("[bold yellow]↺ Reset to defaults (not yet saved)[/bold yellow]")
        self.notify(
            "Reset to defaults — click Save to persist", title="Settings", timeout=3
        )

    def refresh_data(self) -> None:
        """No-op — settings are user-driven."""
