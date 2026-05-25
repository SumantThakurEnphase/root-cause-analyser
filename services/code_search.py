"""
Code Search service — queries ChromaDB for relevant source code snippets.
"""

from typing import Optional

import chromadb
from config import config


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

    def format_snippets_for_prompt(self, snippets: list[dict]) -> str:
        """Format code search results into a readable string for the LLM."""
        if not snippets:
            return "No relevant code snippets found."

        parts = []
        for i, s in enumerate(snippets, 1):
            header = f"--- Snippet {i} [{s['repo']}] {s['file_path']} (chunk {s['chunk_index']+1}/{s['total_chunks']}, relevance: {s['score']}) ---"
            parts.append(f"{header}\n{s['code']}")

        return "\n\n".join(parts)
