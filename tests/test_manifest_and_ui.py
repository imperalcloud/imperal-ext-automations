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


def test_manifest_version_is_1_7_0():
    m = _manifest()
    assert m["version"] == "1.7.0"
