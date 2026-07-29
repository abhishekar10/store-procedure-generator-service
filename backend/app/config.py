"""Paths, environment variables, and model names shared across the backend."""

import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent          # backend/app
BACKEND_DIR = APP_DIR.parent                        # backend
PROJECT_ROOT = BACKEND_DIR.parent                    # 05 - RAG Project
RAG_ROOT = PROJECT_ROOT.parent                       # rag

load_dotenv(RAG_ROOT / ".env")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

GENERATION_MODEL = "models/gemini-flash-latest"
EMBEDDING_MODEL = "models/gemini-embedding-001"

REFERENCE_DB_PATH = BACKEND_DIR / "db" / "reference.db"
SCHEMA_METADATA_PATH = BACKEND_DIR / "db" / "metadata.json"
SP_TEMPLATES_DIR = APP_DIR / "sp_templates"

DATA_DIR = BACKEND_DIR / "data"
VECTOR_STORE_DIR = DATA_DIR / "vector_store"
APP_DB_PATH = DATA_DIR / "app.db"

SCHEMA_COLLECTION = "schema_chunks"
PATTERN_COLLECTION = "pattern_templates"

PATTERN_CATEGORIES = [
    "pre_validation",
    "temp_tables",
    "backup",
    "post_validation",
    "rollback_auto",
    "rollback_manual",
]
