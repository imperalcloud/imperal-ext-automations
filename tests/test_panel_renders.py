"""The panels must actually BUILD -- not merely contain the right words.

Why this file exists
--------------------
"Open Editor" opened an empty centre panel for days while the whole suite was
green. It could not have been caught, because every UI test in
test_manifest_and_ui.py greps panel SOURCE for strings:

    assert 'submit_label="Save changes"' in src

That text was present and perfect. The form still could not be built: it
referenced `ui.Hidden`, a primitive the SDK has never had, and passed
`label=` to `ui.TextArea`, which takes no such argument. Both raise only when
the tree is CONSTRUCTED, and the panel handler builds it on click -- so the
render died, the panel returned an error, and the centre stayed blank.

A grep can only prove a string is present. These tests prove the panel RUNS:

  * test_edit_form_builds / test_editor_card_builds -- construct the real
    editor with a realistic rule, the exact path that was broken;
  * test_every_ui_call_matches_the_installed_sdk -- an AST sweep of every
    `ui.X(...)` in the extension against the installed SDK's real signatures.
    That one is the durable net: it fails for ANY future component or kwarg
    that does not exist, in any panel file, without anyone remembering to
    write a test for it.

Deliberately no mocking of `ui`: the point is to fail exactly when the SDK the
extension will run against cannot build the tree.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib

import imperal_sdk.ui as ui
import pytest

import panels
import panels_center

EXT_ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _rule(**over):
    """A rule shaped the way the gateway really returns one.

    Structured conn-ssh action on purpose: that is the shape whose editor was
    broken, and the shape a server automation stores.
    """
    rule = {
        "id": 346,
        "user_id": "imp_u_test_user_001",
        "status": "active",
        "prompt": "When system.scheduled: check uptime",
        "interpretation": "check uptime on nl-node1",
        "trigger_filter": {"event_type": "system.scheduled", "schedule": "0 7 * * *"},
        "actions": [{
            "app_id": "conn-ssh",
            "tool": "run_command",
            "args": {"connection_id": "nl-node1", "command": "uptime"},
        }],
        "cooldown_seconds": 300,
        "notify_mode": "all",
    }
    rule.update(over)
    return rule


def _tree(node) -> str:
    """Serialise a built UI tree so we can assert on what it carries."""
    return json.dumps(node, default=lambda o: getattr(o, "__dict__", str(o)),
                      ensure_ascii=False)


# --------------------------------------------------------------------------
# 1. The editor -- the surface that was blank
# --------------------------------------------------------------------------

def test_edit_form_builds():
    """Constructing the edit form must not raise (it used to: ui.Hidden)."""
    blob = _tree(panels_center._edit_form(None, _rule()))
    assert len(blob) > 200, "edit form came back suspiciously empty"


def test_edit_form_carries_rule_id_so_the_save_targets_the_right_rule():
    """rule_id must ride along, or 'Save changes' updates nothing.

    It travels via ui.Form(defaults=...) -- the SDK's documented way to attach
    fixed context, since there is no ui.Hidden primitive.
    """
    blob = _tree(panels_center._edit_form(None, _rule(id=4242)))
    assert "rule_id" in blob
    assert "4242" in blob


def test_edit_form_keeps_the_action_field_labelled():
    """The action box needs its human caption; losing it while fixing the
    illegal `label=` kwarg would trade one UX bug for another."""
    blob = _tree(panels_center._edit_form(None, _rule()))
    assert "What should happen" in blob


def test_edit_form_prefills_the_stored_action_in_words():
    """A structured action has no `message` key, so the editor must render it
    through describe_actions -- otherwise the field shows up blank and a save
    would wipe the user's pre-authorized command."""
    blob = _tree(panels_center._edit_form(None, _rule()))
    assert "uptime" in blob


@pytest.mark.parametrize("rule", [None, _rule()], ids=["create", "edit"])
def test_editor_card_builds_in_both_modes(rule):
    """The card wraps create AND edit; only edit was broken, which is exactly
    why both belong here."""
    assert len(_tree(panels_center._editor_card(None, rule))) > 200


# --------------------------------------------------------------------------
# 2. The sidebar action preview -- same class of bug, other file
# --------------------------------------------------------------------------

@pytest.mark.parametrize("actions,ids", [
    ([{"app_id": "conn-ssh", "tool": "run_command",
       "args": {"connection_id": "nl-node1", "command": "uptime"}}], "ssh"),
    ([{"app_id": "notes", "tool": "create_note", "args": {"title": "x"}}], "extension"),
    ([{"message": "email me the summary"}], "free_text"),
    ([], "no_action"),
])
def test_sidebar_action_preview_builds_for_every_action_shape(actions, ids):
    assert _tree(panels._rule_action_preview(_rule(actions=actions)))


def test_sidebar_shows_the_pre_authorized_command_and_server():
    """For a server rule the stored command IS the consent record, so it has to
    be legible in the panel -- an unreadable pre-authorization is not one."""
    blob = _tree(panels._rule_action_preview(_rule()))
    assert "nl-node1" in blob
    assert "uptime" in blob


# --------------------------------------------------------------------------
# 3. The durable net: every ui.* call must exist in the installed SDK
# --------------------------------------------------------------------------

def _ui_calls():
    """Yield (file, line, component, kwargs) for every `ui.X(...)` call."""
    for path in sorted(EXT_ROOT.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "ui"):
                continue
            kwargs = [kw.arg for kw in node.keywords if kw.arg]
            yield path.name, node.lineno, func.attr, kwargs


def test_every_ui_call_matches_the_installed_sdk():
    """Catch the whole bug class: unknown component OR unknown keyword.

    This is what `ui.Hidden` and `ui.TextArea(label=...)` needed. Both are
    invisible to import checks and to source greps -- they explode only when a
    panel builds its tree, i.e. in front of the user.
    """
    problems = []
    for filename, lineno, component, kwargs in _ui_calls():
        target = getattr(ui, component, None)
        if target is None:
            problems.append(
                f"{filename}:{lineno} ui.{component} does not exist in imperal_sdk.ui"
            )
            continue
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            continue
        if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
            continue
        for kw in kwargs:
            if kw not in sig.parameters:
                problems.append(
                    f"{filename}:{lineno} ui.{component} has no '{kw}' "
                    f"(accepts: {', '.join(sorted(sig.parameters))})"
                )

    assert not problems, (
        "panel code uses UI that the installed SDK cannot build:\n  "
        + "\n  ".join(problems)
    )


def test_the_sweep_actually_inspects_something():
    """Guard the guard: if the AST walk silently found nothing, the test above
    would pass forever while checking exactly zero calls."""
    calls = list(_ui_calls())
    assert len(calls) > 20, f"only {len(calls)} ui.* calls found -- sweep looks broken"
    assert {c[0] for c in calls} >= {"panels.py", "panels_center.py"}
