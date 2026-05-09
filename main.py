"""Automations v1.4.0 · AI Cloud Agents — entry point.

Loaded by ICNLI OS Kernel via spec_from_file_location.exec_module.
Module purge enables hot-reload across redeploys; sys.path insert
mirrors the kernel-side loader so this file also runs under direct
`python main.py` during local dev.
"""
from __future__ import annotations

import os
import sys

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

_MODULES = (
    "app", "api", "constants", "models",
    "cache_models",
    "handlers", "skeleton", "panels",
)
for _m in [k for k in sys.modules if k in _MODULES]:
    del sys.modules[_m]

from app import ext, chat       # noqa: E402, F401

# Register cache models BEFORE any submodule that touches ctx.cache
# (handlers + skeleton both go through load_event_catalog_cached).
import cache_models              # noqa: E402, F401

import handlers                  # noqa: E402, F401
import skeleton                  # noqa: E402, F401
import panels                    # noqa: E402, F401
