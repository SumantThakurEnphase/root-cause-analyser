"""
Code Search service — queries ChromaDB for relevant source code snippets.
Supports call-chain following: after initial search, parses snippets for
require/import references and function calls, then fetches those too.
"""

import re
from typing import Optional

import chromadb
from config import config

# Patterns to extract references from code snippets
_REQUIRE_RE = re.compile(r"require\(['\"]([^'\"]+)['\"]\)")
_IMPORT_RE = re.compile(r"from\s+['\"]([^'\"]+)['\"]")
_FUNCTION_CALL_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
# Common noise words that aren't useful function names
_IGNORE_FUNCTIONS = {
    "if", "for", "while", "switch", "catch", "return", "throw",
    "require", "import", "from", "module", "exports", "console",
    "log", "error", "warn", "info", "debug", "toString", "valueOf",
    "JSON", "Object", "Array", "String", "Number", "Boolean", "Promise",
    "resolve", "reject", "then", "catch", "finally", "map", "filter",
    "reduce", "forEach", "includes", "push", "pop", "join", "split",
    "describe", "it", "before", "after", "beforeEach", "afterEach",
    "expect", "assert", "sinon", "stub", "spy", "mock",
}


class CodeSearchService:
    def __init__(self):
        self._client: Optional[chromadb.HttpClient] = None
        self._collection: Optional[chromadb.Collection] = None

    def _ensure_connected(self) -> chromadb.Collection:
        if self._collection is None:
            self._client = chromadb.HttpClient(
                host=config.CHROMADB_HOST,
                port=config.CHROMADB_PORT,
            )
            self._collection = self._client.get_or_create_collection(
                name=config.CHROMADB_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        repo_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Search the indexed codebase for snippets relevant to the query.

        Args:
            query: Natural language or error message to search for.
            top_k: Number of results to return (default from config).
            repo_filter: Optional repo name to restrict search.

        Returns:
            List of dicts with keys: code, file_path, repo, score
        """
        collection = self._ensure_connected()
        top_k = top_k or config.CODE_SEARCH_TOP_K

        where_filter = {"repo": repo_filter} if repo_filter else None

        try:
            results = collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            print(f"ChromaDB search error: {e}")
            return []

        snippets = []
        if results and results["documents"]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)
            distances = results["distances"][0] if results["distances"] else [0.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, distances):
                snippets.append(
                    {
                        "code": doc,
                        "file_path": meta.get("file_path", "unknown"),
                        "repo": meta.get("repo", "unknown"),
                        "chunk_index": meta.get("chunk_index", 0),
                        "total_chunks": meta.get("total_chunks", 1),
                        "score": round(1 - dist, 4),  # cosine similarity
                    }
                )

        return snippets

    def search_with_call_chain(
        self,
        query: str,
        top_k: Optional[int] = None,
        repo_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Two-pass search: initial semantic search, then follow-up for
        referenced files and called functions found in the first-pass snippets.

        This ensures that if index.js is found and it imports getSiteGeometry
        from './utils/helpers', the helpers file is also retrieved — giving
        the LLM visibility into the full call chain (including async signatures).
        """
        top_k = top_k or config.CODE_SEARCH_TOP_K

        # Pass 1: standard semantic search
        initial_snippets = self.search(query, top_k=top_k, repo_filter=repo_filter)
        if not initial_snippets:
            return initial_snippets

        # Extract references from the initial snippets
        ref_queries = self._extract_references(initial_snippets)
        if not ref_queries:
            return initial_snippets

        # Pass 2: search for each extracted reference
        seen_keys = {
            (s["repo"], s["file_path"], s["chunk_index"]) for s in initial_snippets
        }
        followup_snippets: list[dict] = []

        for ref_query in ref_queries:
            extra = self.search(ref_query, top_k=3, repo_filter=repo_filter)
            for s in extra:
                key = (s["repo"], s["file_path"], s["chunk_index"])
                if key not in seen_keys:
                    seen_keys.add(key)
                    followup_snippets.append(s)

        # Merge: initial first, then follow-ups sorted by score, capped
        max_total = top_k * 2
        combined = initial_snippets + sorted(
            followup_snippets, key=lambda s: s["score"], reverse=True
        )
        combined = combined[:max_total]

        print(
            f"[CodeSearch] Pass 1: {len(initial_snippets)} snippets, "
            f"Pass 2: {len(followup_snippets)} new snippets from {len(ref_queries)} references, "
            f"Total: {len(combined)}"
        )
        return combined

    @staticmethod
    def _extract_references(snippets: list[dict]) -> list[str]:
        """
        Parse code snippets for require/import paths and meaningful function
        calls. Returns a deduplicated list of search queries for follow-up.
        """
        ref_set: set[str] = set()

        for snippet in snippets:
            code = snippet.get("code", "")

            # Extract require('...') and import ... from '...' paths
            for match in _REQUIRE_RE.finditer(code):
                path = match.group(1)
                # Only follow relative/project paths, skip node_modules
                if not path.startswith("."):
                    continue
                # Use the last segment as a search term (e.g., './utils/helpers' -> 'helpers')
                base = path.rstrip("/").split("/")[-1]
                if base:
                    ref_set.add(base)

            for match in _IMPORT_RE.finditer(code):
                path = match.group(1)
                if not path.startswith("."):
                    continue
                base = path.rstrip("/").split("/")[-1]
                if base:
                    ref_set.add(base)

            # Extract function calls that look like project-specific functions
            for match in _FUNCTION_CALL_RE.finditer(code):
                fn_name = match.group(1)
                if fn_name in _IGNORE_FUNCTIONS:
                    continue
                # Only keep names that look meaningful (camelCase or snake_case, 4+ chars)
                if len(fn_name) >= 4 and not fn_name.isupper():
                    ref_set.add(fn_name)

        return list(ref_set)

    def format_snippets_for_prompt(self, snippets: list[dict]) -> str:
        """Format code search results into a readable string for the LLM."""
        if not snippets:
            return "No relevant code snippets found."

        parts = []
        for i, s in enumerate(snippets, 1):
            header = f"--- Snippet {i} [{s['repo']}] {s['file_path']} (chunk {s['chunk_index']+1}/{s['total_chunks']}, relevance: {s['score']}) ---"
            parts.append(f"{header}\n{s['code']}")

        return "\n\n".join(parts)
