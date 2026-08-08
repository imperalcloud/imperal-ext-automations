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


# --------------------------------------------------------------------------
# The sidebar must never outgrow the kernel's reply cap
# --------------------------------------------------------------------------
#
# The left panel "just disappeared" for admins. Nothing threw and every test
# here stayed green, because the panel BUILT perfectly -- it was simply too
# big. The kernel caps a fast-RPC reply at 256KB
# (REPLY_PAYLOAD_MAX_BYTES, imperal_kernel/rpc/stream_consumer.py) and an
# oversize reply is not trimmed: it is REPLACED by a typed error carrying no
# ui at all. The panel host then marks the slot missing and renders nothing
# for it -- not even a spinner -- so the whole left panel vanishes.
#
# An admin sees every rule in the tenant and a list item costs ~3.5KB on the
# wire, so the sidebar crossed the cap at ~70 rules. These tests pin the
# invariant at rule counts far past that.

REPLY_CAP_BYTES = 256 * 1024

_LONG_RU = (
    "Каждый час проверить доступность и валидность файла "
    "/opt/whm-ha-cloud/fleet-status.txt на узлах nl-node1 и sg-node3. "
    "Если хотя бы один источник доступен и валиден, не формировать и не "
    "отправлять OK-отчёт. Если оба источника недоступны или невалидны, "
    "сформировать CRITICAL отчёт с точным текстом «оба источника "
    "fleet-status недоступны/невалидны»."
)


def _bulk_rule(i: int) -> dict:
    """A rule shaped like the heaviest ones really in production: a long
    natural-language prompt and no structured action to summarise."""
    failing = i % 4 == 0
    return {
        "id": 900000 + i,
        "rule_id": 900000 + i,
        "user_id": f"imp_u_owner{i % 7}",
        "status": "active",
        "prompt": "When system.scheduled: " + _LONG_RU,
        "interpretation": _LONG_RU,
        "actions": [{"app_id": None, "tool": None, "args": {}, "message": _LONG_RU}],
        "trigger_filter": {"event_type": "system.scheduled", "schedule": "0 * * * *"},
        "schedule": "0 * * * *",
        "cooldown_seconds": 60,
        "notify_mode": "all",
        "trigger_count": 10,
        "success_count": 0 if failing else 10,
        "fail_count": 10 if failing else 0,
        "last_error": "connection_not_found" if failing else None,
        "last_triggered": "2026-08-08T10:00:00Z",
        "created_at": "2026-08-01T10:00:00Z",
        "lifetime_cost_usd": 0.0123,
        "lifetime_tokens": 19500,
    }


def _render_sidebar_as_admin(monkeypatch, rules):
    """Build the sidebar the way the kernel does, with the gateway stubbed."""
    async def _rules(ctx, tenant_id="default"):
        return rules

    async def _role(ctx):
        return "admin"          # the case that broke: admins see EVERY rule

    monkeypatch.setattr(panels, "list_active_rules", _rules)
    monkeypatch.setattr(panels, "fetch_user_role_cached", _role)

    from imperal_sdk.testing import MockContext
    import asyncio
    node = asyncio.run(
        panels.automations_sidebar(MockContext(user_id="imp_u_admin", tenant_id="default"))
    )
    return node, json.dumps(node.to_dict(), ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("count", [0, 1, 57, 71, 200, 500])
def test_sidebar_reply_stays_under_the_kernel_cap(monkeypatch, count):
    """At ANY rule count the reply must fit, or the panel disappears."""
    _, payload = _render_sidebar_as_admin(monkeypatch, [_bulk_rule(i) for i in range(count)])
    assert len(payload) < REPLY_CAP_BYTES, (
        f"{count} rules -> {len(payload) / 1024:.1f}KB exceeds the "
        f"{REPLY_CAP_BYTES / 1024:.0f}KB reply cap; the left panel would vanish"
    )


def test_sidebar_keeps_failing_rules_when_it_has_to_truncate(monkeypatch):
    """Truncation must never cost the admin a broken rule.

    Rules needing attention are rendered first, so anything dropped for the
    byte budget is healthy -- never failing.
    """
    rules = [_bulk_rule(i) for i in range(300)]
    failing_ids = {r["id"] for r in rules if r["fail_count"]}

    node, payload = _render_sidebar_as_admin(monkeypatch, rules)
    rendered = payload.decode("utf-8")

    shown_failing = sum(1 for rid in failing_ids if f'"{rid}"' in rendered)
    assert shown_failing > 0, "truncation dropped every failing rule"

    # Whatever is rendered must be attention-first: no healthy rule may be
    # shown while a failing one was dropped.
    healthy_ids = {r["id"] for r in rules if not r["fail_count"]}
    shown_healthy = sum(1 for rid in healthy_ids if f'"{rid}"' in rendered)
    if shown_healthy:
        assert shown_failing == len(failing_ids) or shown_healthy == 0, (
            "healthy rules rendered while failing rules were truncated away"
        )


def test_sidebar_says_so_when_rules_are_hidden(monkeypatch):
    """Silently dropping rules would be a lie; the panel must admit it."""
    node, payload = _render_sidebar_as_admin(monkeypatch, [_bulk_rule(i) for i in range(300)])
    rendered = payload.decode("utf-8")
    assert "not shown" in rendered, "hidden rules are not disclosed in the panel"
    assert "Rules (" in rendered


def test_sidebar_shows_every_rule_when_they_all_fit(monkeypatch):
    """The budget must not truncate a normal-sized account."""
    rules = [_bulk_rule(i) for i in range(12)]
    node, payload = _render_sidebar_as_admin(monkeypatch, rules)
    rendered = payload.decode("utf-8")
    for r in rules:
        assert f'"{r["id"]}"' in rendered, f"rule {r['id']} missing though all 12 fit"
    assert "not shown" not in rendered
