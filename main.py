"""Automations v1.3.0 · AI Cloud Agents management + DUI panels."""
from __future__ import annotations

import sys, os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
for _m in [k for k in sys.modules if k in ("app", "handlers", "skeleton", "panels")]:
    del sys.modules[_m]

from app import ext, chat  # noqa: F401

import handlers  # noqa: F401
import skeleton  # noqa: F401
import panels    # noqa: F401
