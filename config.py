import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = "gemini-2.5-flash"
    # Print key
    print("GEMINI_API_KEY:", GEMINI_API_KEY)

    # Microsoft Teams Bot
    MICROSOFT_APP_ID: str = os.getenv("MICROSOFT_APP_ID", "")
    MICROSOFT_APP_PASSWORD: str = os.getenv("MICROSOFT_APP_PASSWORD", "")

    # ChromaDB
    CHROMADB_HOST: str = os.getenv("CHROMADB_HOST", "localhost")
    CHROMADB_PORT: int = int(os.getenv("CHROMADB_PORT", "8000"))
    CHROMADB_COLLECTION: str = "solargraf_codebase"

    # Codebase paths
    REPO_PATHS: dict = {
        "solargraf-api": os.getenv(
            "SOLARGRAF_API_PATH",
            "/Users/suthakur/Desktop/SolarGraf/feature_dev/solargraf-api",
        ),
        "graf-apps": os.getenv(
            "GRAF_APPS_PATH",
            "/Users/suthakur/Desktop/SolarGraf/feature_dev/graf-apps",
        ),
        "design-tool": os.getenv(
            "DESIGN_TOOL_PATH",
            "/Users/suthakur/Desktop/SolarGraf/feature_dev/design-tool",
        ),
    }

    # File extensions to index
    INDEXED_EXTENSIONS: set = {".js", ".ts", ".jsx", ".tsx"}

    # Directories to skip during indexing
    SKIP_DIRS: set = {
        "node_modules",
        "dist",
        "build",
        ".git",
        "coverage",
        ".yarn",
        "test-result",
        ".next",
        ".cache",
    }

    # SigNoz
    SIGNOZ_API_URL: str = os.getenv("SIGNOZ_API_URL", "https://monitoring-develop.solargraf.com/api/v5/query_range")
    SIGNOZ_API_KEY: str = os.getenv("SIGNOZ_API_KEY", "")

    # Embedding model (runs locally via sentence-transformers)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "flax-sentence-embeddings/st-codesearch-distilroberta-base")

    # Code search
    CODE_SEARCH_TOP_K: int = 10
    MAX_CHUNK_CHARS: int = 4000  # fallback max chars for non-function chunks


config = Config()
