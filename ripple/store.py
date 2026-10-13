"""Document store behind one small interface.

LocalStore (keyword search over data/docs/) is the default and the fallback.
HydraStore is used only when HYDRA_DB_API_KEY is set. Every HydraDB call in this
project lives in this file.

If a HydraDB call fails or takes longer than HYDRA_TIMEOUT seconds we log it and
serve the same request from LocalStore. The UI shows the "Powered by HydraDB"
badge only when `served_by` comes back as "hydradb".
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("ripple.store")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DOCS_DIR = DATA_DIR / "docs"
UPLOAD_DIR = DATA_DIR / "uploads"

HYDRA_TIMEOUT = 4
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "what", "which", "that", "this", "it", "its", "with", "if", "we", "i",
    "does", "do", "how", "when", "from", "by", "be", "as", "at", "not",
}


def _terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", (text or "").lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


class Store:
    """doc_id -> text in, {doc, snippet} out."""

    name = "store"

    def add_document(self, doc_id: str, text: str) -> None:
        raise NotImplementedError

    def search(self, query: str, k: int = 3) -> list[dict]:
        raise NotImplementedError


class LocalStore(Store):
    """Keyword search over data/docs/ and anything dropped into data/uploads/."""

    name = "local"

    def _files(self) -> list[Path]:
        files = sorted(DOCS_DIR.glob("*.md")) + sorted(DOCS_DIR.glob("*.txt"))
        if UPLOAD_DIR.exists():
            files += sorted(UPLOAD_DIR.glob("*.md")) + sorted(UPLOAD_DIR.glob("*.txt"))
        return files

    def add_document(self, doc_id: str, text: str) -> None:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", doc_id) or "dropped.md"
        if not name.endswith((".md", ".txt")):
            name += ".md"
        (UPLOAD_DIR / name).write_text(text, encoding="utf-8")

    def search(self, query: str, k: int = 3) -> list[dict]:
        wanted = set(_terms(query))
        if not wanted:
            return []

        scored = []
        for path in self._files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            best, best_score = "", 0
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                if len(para) < 40:  # headings and stubs are not evidence
                    continue
                hits = len(wanted & set(_terms(para)))
                if hits > best_score:
                    best, best_score = para, hits
            if best_score:
                scored.append((best_score, path.name, best))

        scored.sort(key=lambda s: -s[0])
        return [
            {"doc": name, "snippet": _trim(snippet), "score": score}
            for score, name, snippet in scored[:k]
        ]


def _trim(text: str, limit: int = 320) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "\u2026"


class HydraStore(Store):
    """HydraDB v2 SDK (hydradb-sdk 2.1.7). Vector + full-text retrieval."""

    name = "hydradb"

    def __init__(self, token: str, database: str, collection: str):
        from hydra_db import HydraDB  # imported lazily: optional dependency

        self._client = HydraDB(token=token, timeout=HYDRA_TIMEOUT)
        self._database = database
        self._collection = collection
        self._ready = False

    def _ensure_database(self) -> None:
        if self._ready:
            return
        opts = {"timeout_in_seconds": HYDRA_TIMEOUT}
        try:
            self._client.databases.create(database=self._database, request_options=opts)
        except Exception as exc:  # already exists is the common, harmless case
            log.info("hydradb: databases.create skipped (%s)", exc)
        status = self._client.databases.status(database=self._database, request_options=opts)
        if not status.data.infra.ready_for_ingestion:
            raise RuntimeError("hydradb database not ready for ingestion yet")
        self._ready = True

    def add_document(self, doc_id: str, text: str) -> None:
        self._ensure_database()
        self._client.context.ingest(
            type="knowledge",
            database=self._database,
            collection=self._collection,
            documents=[(doc_id, text.encode("utf-8"), "text/markdown")],
            document_metadata=json.dumps([{"id": doc_id}]),
            request_options={"timeout_in_seconds": HYDRA_TIMEOUT},
        )

    def search(self, query: str, k: int = 3) -> list[dict]:
        result = self._client.query(
            database=self._database,
            collection=self._collection,
            query=query,
            type="knowledge",
            query_by="hybrid",
            max_results=k,
            request_options={"timeout_in_seconds": HYDRA_TIMEOUT},
        )
        chunks = getattr(result.data, "chunks", None) or []
        return [
            {
                "doc": c.source_title or "hydradb",
                "snippet": _trim(c.chunk_content or ""),
                "score": c.relevancy_score,
            }
            for c in chunks[:k]
        ]


class StoreFacade(Store):
    """Tries HydraDB when configured, always falls back to LocalStore."""

    def __init__(self, primary: Store | None, local: LocalStore):
        self.primary = primary
        self.local = local
        self.served_by = local.name

    @property
    def name(self) -> str:
        return self.primary.name if self.primary else self.local.name

    def add_document(self, doc_id: str, text: str) -> None:
        self.local.add_document(doc_id, text)  # the demo never loses a doc
        if self.primary:
            try:
                self.primary.add_document(doc_id, text)
            except Exception as exc:
                log.warning("hydradb add_document failed, kept local copy: %s", exc)

    def search(self, query: str, k: int = 3) -> list[dict]:
        if self.primary:
            try:
                hits = self.primary.search(query, k)
                if hits:
                    self.served_by = self.primary.name
                    return hits
                log.info("hydradb returned nothing, falling back to local")
            except Exception as exc:
                log.warning("hydradb search failed, falling back to local: %s", exc)
        self.served_by = self.local.name
        return self.local.search(query, k)


_store: StoreFacade | None = None


def get_store() -> StoreFacade:
    """Singleton store. HydraDB is layered on only if a key is present."""
    global _store
    if _store is not None:
        return _store

    local = LocalStore()
    primary = None
    token = os.environ.get("HYDRA_DB_API_KEY", "").strip()
    if token:
        try:
            primary = HydraStore(
                token,
                os.environ.get("HYDRA_DB_DATABASE", "ripple-brain"),
                os.environ.get("HYDRA_DB_COLLECTION", "ripple_docs"),
            )
            log.info("hydradb enabled")
        except Exception as exc:
            log.warning("hydradb unavailable, using LocalStore: %s", exc)
    else:
        log.info("no HYDRA_DB_API_KEY, using LocalStore")

    _store = StoreFacade(primary, local)
    return _store
