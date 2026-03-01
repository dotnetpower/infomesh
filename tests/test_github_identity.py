"""Tests for infomesh.credits.github_identity — GitHub identity resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from infomesh.config import Config, NodeConfig, load_config
from infomesh.credits.github_identity import (
    detect_git_email,
    format_startup_message,
    is_valid_email,
    resolve_github_email,
)
from infomesh.credits.ledger import ActionType, CreditLedger

# --- Email validation -------------------------------------------------------


class TestEmailValidation:
    def test_valid_emails(self):
        assert is_valid_email("user@example.com")
        assert is_valid_email("user.name@domain.co")
        assert is_valid_email("user+tag@gmail.com")
        assert is_valid_email("a@b.cd")

    def test_invalid_emails(self):
        assert not is_valid_email("")
        assert not is_valid_email("not-an-email")
        assert not is_valid_email("@domain.com")
        assert not is_valid_email("user@")
        assert not is_valid_email("user@.com")
        assert not is_valid_email("user@domain")


# --- Git email detection -----------------------------------------------------


class TestDetectGitEmail:
    @patch("subprocess.run")
    def test_detect_success(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "user@example.com\n"
        assert detect_git_email() == "user@example.com"

    @patch("subprocess.run")
    def test_detect_no_config(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        assert detect_git_email() is None

    @patch("subprocess.run")
    def test_detect_invalid_email(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "not-valid\n"
        assert detect_git_email() is None

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_detect_git_not_installed(self, mock_run):
        assert detect_git_email() is None

    @patch("subprocess.run", side_effect=OSError("fail"))
    def test_detect_os_error(self, mock_run):
        assert detect_git_email() is None


# --- Resolve GitHub email -----------------------------------------------------


class TestResolveGithubEmail:
    def test_explicit_config_takes_priority(self):
        """Explicit github_email in config overrides git detection."""
        from dataclasses import replace as dc_replace

        config = Config(
            node=dc_replace(
                NodeConfig(),
                github_email="explicit@example.com",
            )
        )
        with patch(
            "infomesh.credits.github_identity.detect_git_email",
            return_value="git@example.com",
        ):
            result = resolve_github_email(config)
        assert result == "explicit@example.com"

    @patch(
        "infomesh.credits.github_identity.detect_git_email",
        return_value="git@example.com",
    )
    def test_fallback_to_git(self, mock_detect):
        """Falls back to git config when github_email is empty."""
        config = Config()
        result = resolve_github_email(config)
        assert result == "git@example.com"

    @patch(
        "infomesh.credits.github_identity.detect_git_email",
        return_value=None,
    )
    def test_no_identity(self, mock_detect):
        """Returns None when neither config nor git has email."""
        config = Config()
        result = resolve_github_email(config)
        assert result is None


# --- Startup message ---------------------------------------------------------


class TestStartupMessage:
    def test_connected_message(self):
        msg = format_startup_message("user@example.com")
        assert "user@example.com" in msg
        assert "linked" in msg.lower() or "across" in msg.lower()

    def test_not_connected_message(self):
        msg = format_startup_message(None)
        assert "not connected" in msg.lower()
        assert "infomesh config set" in msg or "config github" in msg


# --- Ledger with owner email -------------------------------------------------


class TestLedgerOwnerEmail:
    def test_ledger_default_no_email(self):
        """Ledger without email tracks locally."""
        lg = CreditLedger()
        assert lg.owner_email == ""
        stats = lg.stats()
        assert stats.owner_email == ""
        lg.close()

    def test_ledger_with_email(self):
        """Ledger with email tags entries."""
        lg = CreditLedger(owner_email="user@example.com")
        assert lg.owner_email == "user@example.com"
        lg.record_action(ActionType.CRAWL, 5.0)
        stats = lg.stats()
        assert stats.owner_email == "user@example.com"
        assert stats.total_earned == pytest.approx(5.0)
        lg.close()

    def test_ledger_email_stored_in_entries(self):
        """Owner email is stored alongside credit entries."""
        lg = CreditLedger(owner_email="dev@infomesh.org")
        lg.record_action(ActionType.CRAWL, 1.0)

        # Check raw DB
        row = lg._conn.execute(
            "SELECT owner_email FROM credit_entries ORDER BY entry_id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "dev@infomesh.org"
        lg.close()

    def test_ledger_email_update(self):
        """Owner email can be updated for future entries."""
        lg = CreditLedger(owner_email="old@example.com")
        lg.record_action(ActionType.CRAWL, 1.0)

        lg.owner_email = "new@example.com"
        lg.record_action(ActionType.CRAWL, 1.0)

        rows = lg._conn.execute(
            "SELECT owner_email FROM credit_entries ORDER BY entry_id ASC"
        ).fetchall()
        assert rows[0][0] == "old@example.com"
        assert rows[1][0] == "new@example.com"
        lg.close()

    def test_ledger_migration_adds_column(self):
        """Migration adds owner_email column to existing databases."""
        # Create a ledger (which creates the schema)
        lg = CreditLedger()
        # Verify the column exists
        cursor = lg._conn.execute("PRAGMA table_info(credit_entries)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "owner_email" in columns
        lg.close()


# --- Config persistence -------------------------------------------------------


class TestConfigGithubEmail:
    def test_default_empty(self):
        """Default config has empty github_email."""
        config = Config()
        assert config.node.github_email == ""

    def test_load_from_toml(self, tmp_path: Path):
        """GitHub email loads from TOML config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[node]\ngithub_email = "user@example.com"\n')
        config = load_config(config_file)
        assert config.node.github_email == "user@example.com"

    def test_save_and_reload(self, tmp_path: Path):
        """GitHub email round-trips through save/load."""
        from dataclasses import replace as dc_replace

        from infomesh.config import save_config

        config_file = tmp_path / "config.toml"
        config = Config(
            node=dc_replace(
                NodeConfig(),
                github_email="round@trip.com",
                data_dir=tmp_path,
            )
        )
        save_config(config, config_file)
        reloaded = load_config(config_file)
        assert reloaded.node.github_email == "round@trip.com"


# --- Same-account peering ---------------------------------------------------


class TestSameAccountPeering:
    """Nodes with the same GitHub email can still peer.

    Peering is by Ed25519 peer ID (per-node keys in ~/.infomesh/keys/),
    not by GitHub email. Two nodes with the same email will have
    different peer IDs and can connect normally.
    """

    def test_different_keys_same_email(self, tmp_path: Path):
        """Two nodes with same email get different peer IDs."""
        from infomesh.p2p.keys import ensure_keys

        keys_dir_a = tmp_path / "node_a"
        keys_dir_b = tmp_path / "node_b"
        keys_dir_a.mkdir()
        keys_dir_b.mkdir()

        keys_a = ensure_keys(keys_dir_a)
        keys_b = ensure_keys(keys_dir_b)

        # Same email, different peer IDs
        assert keys_a.peer_id != keys_b.peer_id
        # Each has unique signing capability
        msg = b"test message"
        sig_a = keys_a.sign(msg)
        sig_b = keys_b.sign(msg)
        assert sig_a != sig_b
