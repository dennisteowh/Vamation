#!/usr/bin/env python3
from __future__ import annotations

import runpy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

runpy.run_path(str(PROJECT_ROOT / "app" / "webapp" / "app.py"), run_name="__main__")
