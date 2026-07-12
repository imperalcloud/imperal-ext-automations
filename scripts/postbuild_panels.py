"""Post-build shim: restore full panel metadata into imperal.json.

The installed imperal-sdk 5.9.3 manifest generator emits only
panel_id/slot/title/tree and DROPS icon/refresh/center_overlay/width hints
declared by @ext.panel — which broke the Dev-Portal deploy validator against
tests/test_manifest_and_ui.py (workshop.center_overlay). The SDK fix is
committed (imperal-sdk 65287df) and rides the next release; until the CLI
ships it, run this after `imperal build .`:

    python3 scripts/postbuild_panels.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: F401  — registers panels on the Extension object
from app import ext

manifest_path = Path(__file__).resolve().parent.parent / "imperal.json"
manifest = json.loads(manifest_path.read_text())

by_id = {p["panel_id"]: p for p in manifest.get("panels", [])}
for panel_id, meta in (ext.panels or {}).items():
    entry = by_id.get(panel_id)
    if entry is None:
        continue
    for k, v in meta.items():
        if k in ("tree", "func") or callable(v):
            continue
        entry.setdefault(k, v)

manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
print("panels metadata restored:",
      {pid: sorted(k for k in p if k not in ("panel_id", "slot", "title", "tree"))
       for pid, p in by_id.items()})
