"""Tests for infomesh.dx — developer experience tools."""

from __future__ import annotations

from typing import Any

from infomesh.dx import (
    ChangelogEntry,
    DefaultTokenizer,
    PluginManager,
    generate_changelog,
    generate_tool_guide,
    get_tokenizer,
    set_tokenizer,
)


class _FakePlugin:
    """Minimal plugin for testing."""

    name = "fake_plugin"

    def setup(self, app: Any) -> None:
        pass

    def teardown(self) -> None:
        pass


class TestPluginManager:
    def test_register_and_list(self) -> None:
        pm = PluginManager()
        pm.register(_FakePlugin())
        plugins = pm.list_plugins()
        assert any(p.name == "fake_plugin" for p in plugins)

    def test_setup_all(self) -> None:
        pm = PluginManager()
        pm.register(_FakePlugin())
        pm.setup_all(app=None)  # Should not raise

    def test_teardown_all(self) -> None:
        pm = PluginManager()
        pm.register(_FakePlugin())
        pm.teardown_all()  # Should not raise


class TestTokenizerHook:
    def test_default_tokenizer(self) -> None:
        tok = DefaultTokenizer()
        tokens = tok.tokenize("Hello World python")
        assert "hello" in tokens
        assert "world" in tokens

    def test_set_get_tokenizer(self) -> None:
        orig = get_tokenizer()
        custom = DefaultTokenizer()
        set_tokenizer(custom)
        assert get_tokenizer() is custom
        set_tokenizer(orig)  # restore


class TestToolGuide:
    def test_text_guide(self) -> None:
        guide = generate_tool_guide(format="text")
        assert isinstance(guide, str)
        assert "search" in guide.lower()

    def test_markdown_guide(self) -> None:
        guide = generate_tool_guide(format="markdown")
        assert isinstance(guide, str)
        assert "#" in guide  # Markdown headers


class TestChangelog:
    def test_basic_changelog(self) -> None:
        entries = [
            ChangelogEntry(
                version="0.2.0",
                date="2024-01-15",
                changes=["Added NLP", "Added RAG"],
            ),
            ChangelogEntry(
                version="0.1.0",
                date="2024-01-01",
                changes=["Initial release"],
            ),
        ]
        log = generate_changelog(entries)
        assert "0.2.0" in log
        assert "0.1.0" in log
        assert "NLP" in log
