"""Tiny module that splices `../backend` onto sys.path.

The MCP server is its own uv project with its own venv, so we have to bring
the FastAPI backend's modules (`data_store`, `models`, `agent`) into scope
manually. Doing it here once means every module just `import _path` first
and gets the backend on the path. The longer-term fix is to extract those
into a shared package both projects depend on; tracked as follow-up.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
