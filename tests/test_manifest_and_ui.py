import json
import os


def _manifest():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "imperal.json")) as f:
        return json.load(f)


def test_manifest_has_update_automation_tool():
    m = _manifest()
    names = {t["name"] for t in m.get("tools", m.get("functions", []))}
    assert "update_automation" in names


def test_manifest_create_has_no_max_per_hour():
    m = _manifest()
    tools = m.get("tools", m.get("functions", []))
    create = next(t for t in tools if t["name"] == "create_automation")
    props = create["params_schema"]["properties"]
    assert "max_per_hour" not in props


def test_manifest_version_matches_ext():
    """Manifest version must equal the Extension version declared in app.py.

    Version-agnostic on purpose: a bump no longer breaks this test, but a
    forgotten `imperal build` (manifest drift) still does.
    """
    import re
    m = _manifest()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "app.py")) as f:
        src = f.read()
    mo = re.search(r'version\s*=\s*"([^"]+)"', src)
    assert mo, "version=\"...\" not found in app.py"
    assert m["version"] == mo.group(1), (
        f'manifest version {m["version"]!r} != app.py {mo.group(1)!r} — run `imperal build`'
    )
