"""Pytest bootstrap: make `main` / `services` importable and isolate workspace."""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Keep tests from touching a real GROQ/GEMINI config.
os.environ.setdefault("GROQ_API_KEY", "")
os.environ.setdefault("GEMINI_API_KEY", "")
