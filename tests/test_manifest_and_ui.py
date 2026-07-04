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


def test_manifest_has_workshop_panel_and_refreshes_rule_updates():
    m = _manifest()
    panels = m.get("panels", [])
    workshop = next((p for p in panels if p.get("panel_id") == "workshop"), None)
    assert workshop is not None
    assert workshop.get("slot") == "center"
    assert workshop.get("center_overlay") is True
    assert "rule_updated" in (workshop.get("refresh") or "")


def test_manifest_create_has_no_max_per_hour():
    m = _manifest()
    tools = m.get("tools", m.get("functions", []))
    create = next(t for t in tools if t["name"] == "create_automation")
    props = create["params_schema"]["properties"]
    assert "max_per_hour" not in props


def test_sidebar_has_inline_editor_and_notifications_copy():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "panels.py")) as f:
        src = f.read()
    assert "Open editor" in src
    assert "Notifications" in src
    assert "update_automation" in src


def test_workshop_has_edit_form_fields():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "panels_center.py")) as f:
        src = f.read()
    assert 'param_name="notify_mode"' in src
    assert 'param_name="status"' in src
    assert 'submit_label="Save changes"' in src


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
