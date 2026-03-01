"""Tests for infomesh.sdk.client — Python SDK."""

from __future__ import annotations

import tempfile

from infomesh.sdk.client import InfoMeshClient


class TestInfoMeshClient:
    def test_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = InfoMeshClient(data_dir=tmp)
            assert client is not None

    def test_context_manager(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            InfoMeshClient(data_dir=tmp) as client,
        ):
            assert client is not None

    def test_search(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            InfoMeshClient(data_dir=tmp) as client,
        ):
            results = client.search("test query")
            assert isinstance(results, list)


class TestInfoMeshClientIntegrations:
    """Tests for infomesh.integrations modules — LangChain, LlamaIndex, Haystack."""

    def test_langchain_retriever_init(self) -> None:
        from infomesh.integrations.langchain import InfoMeshRetriever

        retriever = InfoMeshRetriever()
        assert retriever is not None

    def test_llamaindex_reader_init(self) -> None:
        from infomesh.integrations.llamaindex import InfoMeshReader

        reader = InfoMeshReader()
        assert reader is not None

    def test_haystack_store_init(self) -> None:
        from infomesh.integrations.haystack import InfoMeshDocumentStore

        store = InfoMeshDocumentStore()
        assert store is not None
