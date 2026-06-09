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
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import chromadb
from chromadb.utils.embedding_functions.sentence_transformer_embedding_function import SentenceTransformerEmbeddingFunction

# Add parent dir so config is importable when run as script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

# ---------------------------------------------------------------------------
# Regex patterns that mark the start of a JS/TS function, class, or method.
# We look for these at the beginning of a line (after optional whitespace).
# ---------------------------------------------------------------------------
_FUNC_START_RE = re.compile(
    r"^[ \t]*"
    r"(?:"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*\w*\s*\("
    r"|"
    r"(?:export\s+)?(?:default\s+)?class\s+\w+"
    r"|"
    r"(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>"
    r"|"
    r"(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?function\s*\*?\s*\("
    r"|"
    r"(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?\w+\s*\([^)]*\)\s*\{"
    r")",
    re.MULTILINE,
)

# Pattern to extract a human-readable name from the first line of a chunk
_FUNC_NAME_RE = re.compile(
    r"(?:function\s*\*?\s+(\w+))"
    r"|(?:class\s+(\w+))"
    r"|(?:(?:const|let|var)\s+(\w+)\s*=)"
    r"|(?:(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?(\w+)\s*\()"
)


def _extract_func_name(line: str) -> str:
    """Try to pull a function / class name from the opening line."""
    m = _FUNC_NAME_RE.search(line)
    if m:
        return next((g for g in m.groups() if g), "anonymous")
    return "anonymous"


def _find_block_end(content: str, start: int) -> int:
    """Given a position *before* the opening '{', find matching '}' using brace counting."""
    depth = 0
    i = start
    in_string = None  # tracks quote char
    in_line_comment = False
    in_block_comment = False
    length = len(content)

    while i < length:
        ch = content[i]
        prev = content[i - 1] if i > 0 else ""

        # --- handle comments ---
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "/" and prev == "*":
                in_block_comment = False
            i += 1
            continue
        if ch == "/" and i + 1 < length:
            nxt = content[i + 1]
            if nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if nxt == "*":
                in_block_comment = True
                i += 2
                continue

        # --- handle strings ---
        if in_string:
            if ch == in_string and prev != "\\":
                in_string = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_string = ch
            i += 1
            continue

        # --- brace counting ---
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i  # position of closing brace

        i += 1

    return length - 1  # fallback: end of content


def _chunk_file_by_functions(content: str, max_chars: int) -> list[dict]:
    """
    Split JS/TS source into function-level chunks.

    Returns a list of dicts: {"code": str, "name": str}
    - Each recognised function / class / arrow-fn becomes its own chunk.
    - Top-level code between functions is grouped as an "imports_and_setup" chunk.
    - Any chunk that exceeds *max_chars* is further split on newlines.
    """
    chunks: list[dict] = []
    matches = list(_FUNC_START_RE.finditer(content))

    if not matches:
        # No recognisable functions → fall back to line-based splitting
        for piece in _split_large(content, max_chars):
            chunks.append({"code": piece, "name": "module"})
        return chunks

    prev_end = 0

    for match in matches:
        func_start = match.start()

        # Capture top-level code *before* this function
        if func_start > prev_end:
            preamble = content[prev_end:func_start].strip()
            if preamble:
                for piece in _split_large(preamble, max_chars):
                    chunks.append({"code": piece, "name": "imports_and_setup"})

        # Find the opening brace for this block
        brace_pos = content.find("{", match.start())
        if brace_pos == -1:
            # Arrow fn without braces or declaration — take until next match or EOF
            next_start = matches[matches.index(match) + 1].start() if match != matches[-1] else len(content)
            block = content[func_start:next_start].strip()
        else:
            block_end = _find_block_end(content, brace_pos)
            block = content[func_start:block_end + 1].strip()

        first_line = block.split("\n", 1)[0]
        name = _extract_func_name(first_line)

        for piece in _split_large(block, max_chars):
            chunks.append({"code": piece, "name": name})

        prev_end = func_start + len(block)
        # Advance past any trailing whitespace in original content
        while prev_end < len(content) and content[prev_end] in (" ", "\t", "\n", "\r"):
            prev_end += 1

    # Trailing code after last function
    if prev_end < len(content):
        tail = content[prev_end:].strip()
        if tail:
            for piece in _split_large(tail, max_chars):
                chunks.append({"code": piece, "name": "module_tail"})

    return chunks


def _split_large(text: str, max_chars: int) -> list[str]:
    """Split text that exceeds max_chars on newline boundaries."""
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    lines = text.split("\n")
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current:
            pieces.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        pieces.append("\n".join(current))

    return pieces


def _should_skip(dirpath: str) -> bool:
    parts = dirpath.replace("\\", "/").split("/")
    return any(part in config.SKIP_DIRS for part in parts)


def _stable_id(repo: str, rel_path: str, chunk_idx: int) -> str:
    raw = f"{repo}:{rel_path}:{chunk_idx}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def index_repo(
    collection: chromadb.Collection,
    repo_name: str,
    repo_path: str,
) -> dict:
    """Index a single repo into ChromaDB. Returns a stats dict."""
    repo_start = time.time()

    if not os.path.isdir(repo_path):
        print(f"  ⚠ Repo path not found, skipping: {repo_path}")
        return {"repo": repo_name, "files": 0, "chunks": 0, "elapsed": 0.0}

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
            func_chunks = _chunk_file_by_functions(content, config.MAX_CHUNK_CHARS)

            for idx, chunk_info in enumerate(func_chunks):
                doc_id = _stable_id(repo_name, rel_path, idx)
                documents.append(chunk_info["code"])
                metadatas.append(
                    {
                        "repo": repo_name,
                        "file_path": rel_path,
                        "function_name": chunk_info["name"],
                        "chunk_index": idx,
                        "total_chunks": len(func_chunks),
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

    repo_elapsed = time.time() - repo_start
    print(f"  ✓ {repo_name}: {file_count} files → {total} chunks indexed in {repo_elapsed:.1f}s")
    return {"repo": repo_name, "files": file_count, "chunks": total, "elapsed": repo_elapsed}


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

    embedding_fn = SentenceTransformerEmbeddingFunction(
        model_name=config.EMBEDDING_MODEL,
        normalize_embeddings=True,
    )
    collection = client.get_or_create_collection(
        name=config.CHROMADB_COLLECTION,
        metadata={"hnsw:space": "cosine"},
        embedding_function=embedding_fn,
    )

    repos_to_index = (
        {args.repo: config.REPO_PATHS[args.repo]}
        if args.repo
        else config.REPO_PATHS
    )

    start = time.time()
    results: list[dict] = []

    if len(repos_to_index) > 1:
        # Index repos in parallel
        print(f"\nIndexing {len(repos_to_index)} repos in parallel...")
        with ThreadPoolExecutor(max_workers=len(repos_to_index)) as executor:
            futures = {
                executor.submit(index_repo, collection, repo_name, repo_path): repo_name
                for repo_name, repo_path in repos_to_index.items()
            }
            for future in as_completed(futures):
                repo_name = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    print(f"  ✗ {repo_name} failed: {exc}")
                    results.append({"repo": repo_name, "files": 0, "chunks": 0, "elapsed": 0.0})
    else:
        # Single repo — no need for threading
        for repo_name, repo_path in repos_to_index.items():
            print(f"\nIndexing {repo_name}...")
            results.append(index_repo(collection, repo_name, repo_path))

    elapsed = time.time() - start
    total_chunks = sum(r["chunks"] for r in results)
    total_files = sum(r["files"] for r in results)

    # Summary table
    print("\n" + "=" * 60)
    print(f"{'Repo':<20} {'Files':>8} {'Chunks':>8} {'Time (s)':>10}")
    print("-" * 60)
    for r in sorted(results, key=lambda x: x["repo"]):
        print(f"{r['repo']:<20} {r['files']:>8} {r['chunks']:>8} {r['elapsed']:>10.1f}")
    print("-" * 60)
    print(f"{'TOTAL':<20} {total_files:>8} {total_chunks:>8} {elapsed:>10.1f}")
    print("=" * 60)
    print(f"\n✅ Done! {total_chunks} total chunks indexed in {elapsed:.1f}s")
    print(f"   Collection: {config.CHROMADB_COLLECTION}")


if __name__ == "__main__":
    main()
