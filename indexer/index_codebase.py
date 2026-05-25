"""
Codebase Indexer — Walks solargraf-api, graf-apps, and design-tool repos,
chunks source files, and indexes them into ChromaDB for semantic search.

Usage:
    python -m indexer.index_codebase          # index all repos
    python -m indexer.index_codebase --repo solargraf-api   # index one repo
"""

import argparse
import hashlib
import os
import sys
import time

import chromadb

# Add parent dir so config is importable when run as script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config


def _should_skip(dirpath: str) -> bool:
    parts = dirpath.replace("\\", "/").split("/")
    return any(part in config.SKIP_DIRS for part in parts)


def _chunk_file(content: str, max_size: int) -> list[str]:
    """Split file content into chunks, trying to break on newlines."""
    if len(content) <= max_size:
        return [content]

    chunks = []
    lines = content.split("\n")
    current_chunk: list[str] = []
    current_size = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_size + line_len > max_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_size = 0
        current_chunk.append(line)
        current_size += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def _stable_id(repo: str, rel_path: str, chunk_idx: int) -> str:
    raw = f"{repo}:{rel_path}:{chunk_idx}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def index_repo(
    collection: chromadb.Collection,
    repo_name: str,
    repo_path: str,
) -> int:
    """Index a single repo into ChromaDB. Returns number of chunks indexed."""
    if not os.path.isdir(repo_path):
        print(f"  ⚠ Repo path not found, skipping: {repo_path}")
        return 0

    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    file_count = 0

    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if d not in config.SKIP_DIRS]

        if _should_skip(dirpath):
            continue

        for fname in filenames:
            ext = os.path.splitext(fname)[1]
            if ext not in config.INDEXED_EXTENSIONS:
                continue

            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, repo_path)

            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            if not content.strip():
                continue

            file_count += 1
            chunks = _chunk_file(content, config.MAX_CHUNK_SIZE)

            for idx, chunk in enumerate(chunks):
                doc_id = _stable_id(repo_name, rel_path, idx)
                documents.append(chunk)
                metadatas.append(
                    {
                        "repo": repo_name,
                        "file_path": rel_path,
                        "chunk_index": idx,
                        "total_chunks": len(chunks),
                        "file_extension": ext,
                    }
                )
                ids.append(doc_id)

    # Batch upsert (ChromaDB max batch = 5461)
    batch_size = 5000
    total = len(documents)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        collection.upsert(
            documents=documents[start:end],
            metadatas=metadatas[start:end],
            ids=ids[start:end],
        )

    print(f"  ✓ {repo_name}: {file_count} files → {total} chunks indexed")
    return total


def main():
    parser = argparse.ArgumentParser(description="Index codebase into ChromaDB")
    parser.add_argument(
        "--repo",
        choices=list(config.REPO_PATHS.keys()),
        help="Index only a specific repo (default: all)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing collection before indexing",
    )
    args = parser.parse_args()

    print(f"Connecting to ChromaDB at {config.CHROMADB_HOST}:{config.CHROMADB_PORT}...")
    client = chromadb.HttpClient(
        host=config.CHROMADB_HOST,
        port=config.CHROMADB_PORT,
    )

    if args.reset:
        try:
            client.delete_collection(config.CHROMADB_COLLECTION)
            print(f"  ✓ Deleted existing collection '{config.CHROMADB_COLLECTION}'")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=config.CHROMADB_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    repos_to_index = (
        {args.repo: config.REPO_PATHS[args.repo]}
        if args.repo
        else config.REPO_PATHS
    )

    start = time.time()
    total_chunks = 0
    for repo_name, repo_path in repos_to_index.items():
        print(f"\nIndexing {repo_name}...")
        total_chunks += index_repo(collection, repo_name, repo_path)

    elapsed = time.time() - start
    print(f"\n✅ Done! {total_chunks} total chunks indexed in {elapsed:.1f}s")
    print(f"   Collection: {config.CHROMADB_COLLECTION}")


if __name__ == "__main__":
    main()
