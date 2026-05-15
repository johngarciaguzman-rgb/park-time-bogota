"""Vercel entrypoint for Park Time Bogotá.

Vercel loads this module as a Python Function. We import the FastAPI
application from main.py so all routes are handled by the same app.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import app  # noqa: E402,F401
