"""Functional tests for the automations extension's ownership + fleet tools.

Covers what the user actually asked for:
  * every rule reports WHO created it and WHEN
  * an admin can inspect / manage ANY user's rule by id
  * a normal user cannot touch someone else's rule (and is told why)
  * rules can be found by owner, event, text, date, health
  * bulk lifecycle operations exist, preview safely, and refuse blind runs
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Stub `app` (the chat decorator) before importing handlers.
_app = types.ModuleType("app")


class _Chat:
    def function(self, *a, **k):
        def deco(fn):
            return fn
        return deco


_app.chat = _Chat()
sys.modules.setdefault("app", _app)

import handlers as h  # noqa: E402


# ─── fixtures ─────────────────────────────────────────────────────────── #

ALICE = "imp_u_alice000000"
BOB   = "imp_u_bob0000000"

RULES = [
    {
        "id": 101, "user_id": ALICE, "prompt": "Every hour check fleet status",
        "status": "active", "trigger_count": 12, "success_count": 10,
        "fail_count": 2, "last_error": None, "created_at": "2026-08-01T09:00:00Z",
        "trigger_filter": {"event_type": "system.scheduled", "schedule": "0 * * * *"},
        "actions": [{"app_id": "conn-ssh", "tool": "run_command", "args": {"cmd": "uptime"}}],
    },
    {
        "id": 102, "user_id": ALICE, "prompt": "When an invoice arrives, file it",
        "status": "paused", "trigger_count": 0, "success_count": 0,
        "fail_count": 0, "last_error": None, "created_at": "2026-08-05T12:00:00Z",
        "trigger_filter": {"event_type": "email.received"},
        "actions": [{"message": "save the invoice to notes"}],
    },
    {
        "id": 203, "user_id": BOB, "prompt": "Nightly backup report",
        "status": "active", "trigger_count": 5, "success_count": 2,
        "fail_count": 3, "last_error": "smtp timeout", "created_at": "2026-07-20T22:00:00Z",
        "trigger_filter": {"event_type": "system.scheduled", "schedule": "0 2 * * *"},
        "actions": [{"app_id": "mail", "tool": "send", "args": {"to": "ops@x.io"}}],
    },
]


class _User:
    def __init__(self, uid):
        self.imperal_id = uid
        self.tenant_id = "default"


class _Ctx:
    def __init__(self, uid):
        self.user = _User(uid)


@pytest.fixture
def gw(monkeypatch):
    """Fake gateway: in-memory rules + recorded mutations."""
    state = {"rules": [dict(r) for r in RULES], "patched": [], "deleted": []}

    async def _list(ctx, *, tenant_id):
        return [dict(r) for r in state["rules"]]

    async def _patch(ctx, rule_id, patch):
        state["patched"].append((rule_id, patch))
        return True

    async def _delete(ctx, rule_id):
        state["deleted"].append(rule_id)
        state["rules"] = [r for r in state["rules"] if r["id"] != rule_id]
        return True

    monkeypatch.setattr(h, "list_active_rules", _list)
    monkeypatch.setattr(h, "patch_rule", _patch)
    monkeypatch.setattr(h, "delete_rule", _delete)
    return state


def _as_admin(monkeypatch, yes=True):
    async def _is_admin(ctx):
        return yes
    monkeypatch.setattr(h, "_is_admin", _is_admin)


# ─── ownership + detail ───────────────────────────────────────────────── #

def test_rule_summary_reports_owner_and_creation_time():
    """The core ask: every rule says who made it and when."""
    s = h._rule_summary(RULES[0])
    assert s["user_id"] == ALICE
    assert s["created_at"] == "2026-08-01T09:00:00Z"
    assert s["rule_id"] == 101


def test_rule_summary_exposes_trigger_schedule_and_health():
    s = h._rule_summary(RULES[0])
    assert s["event_type"] == "system.scheduled"
    assert s["schedule"] == "0 * * * *"
    assert s["is_scheduled"] is True
    assert s["success_rate"] == 0.833
    assert s["never_triggered"] is False
    assert "conn-ssh.run_command" in s["action_summary"]


def test_never_triggered_and_failing_are_derived():
    assert h._rule_summary(RULES[1])["never_triggered"] is True
    assert h._rule_summary(RULES[2])["is_failing"] is True


@pytest.mark.asyncio
async def test_admin_can_read_another_users_rule(gw, monkeypatch):
    """The bug that blocked auditing: admin reads Bob's rule by id."""
    _as_admin(monkeypatch, True)
    res = await h.fn_get_automation_details(
        _Ctx(ALICE), h.RuleDetailsParams(rule_id=203),
    )
    assert res.status == "success"
    assert res.data["user_id"] == BOB
    assert "owner" in res.summary


@pytest.mark.asyncio
async def test_non_admin_cannot_read_another_users_rule(gw, monkeypatch):
    _as_admin(monkeypatch, False)
    res = await h.fn_get_automation_details(
        _Ctx(ALICE), h.RuleDetailsParams(rule_id=203),
    )
    assert res.status == "error"
    assert "another user" in res.error.lower()


@pytest.mark.asyncio
async def test_missing_rule_is_distinct_from_forbidden(gw, monkeypatch):
    """'Does not exist' must not be confused with 'not yours'."""
    _as_admin(monkeypatch, False)
    res = await h.fn_get_automation_details(
        _Ctx(ALICE), h.RuleDetailsParams(rule_id=99999),
    )
    assert res.status == "error"
    assert "does not exist" in res.error.lower()


# ─── search / filtering ───────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_admin_sees_every_owner_and_can_scope_to_one(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    ctx = _Ctx(ALICE)

    everything = await h.fn_list_automations(ctx, h.ListAutomationsParams())
    assert everything.data["total"] == 3
    assert everything.data["admin_view"] is True

    only_bob = await h.fn_list_automations(ctx, h.ListAutomationsParams(user_id=BOB))
    assert only_bob.data["total"] == 1
    assert only_bob.data["items"][0]["user_id"] == BOB


@pytest.mark.asyncio
async def test_non_admin_is_confined_to_own_rules(gw, monkeypatch):
    _as_admin(monkeypatch, False)
    res = await h.fn_list_automations(_Ctx(ALICE), h.ListAutomationsParams(user_id=BOB))
    assert {r["user_id"] for r in res.data["items"]} == {ALICE}


@pytest.mark.asyncio
@pytest.mark.parametrize("kw,expected", [
    (dict(event_type="email"),          {102}),
    (dict(search="backup"),             {203}),
    (dict(search="run_command"),        {101}),
    (dict(scheduled_only=True),         {101, 203}),
    (dict(failing_only=True),           {101, 203}),
    (dict(never_triggered=True),        {102}),
    (dict(status="paused"),             {102}),
    (dict(created_after="2026-08-01"),  {101, 102}),
    (dict(created_before="2026-07-31"), {203}),
])
async def test_each_filter_selects_the_right_rules(gw, monkeypatch, kw, expected):
    _as_admin(monkeypatch, True)
    res = await h.fn_list_automations(_Ctx(ALICE), h.ListAutomationsParams(**kw))
    assert {r["rule_id"] for r in res.data["items"]} == expected


@pytest.mark.asyncio
async def test_sort_orders_are_stable(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    ctx = _Ctx(ALICE)
    newest = await h.fn_list_automations(ctx, h.ListAutomationsParams(sort="newest"))
    oldest = await h.fn_list_automations(ctx, h.ListAutomationsParams(sort="oldest"))
    assert newest.data["items"][0]["rule_id"] == 102
    assert oldest.data["items"][0]["rule_id"] == 203


# ─── owner stats ──────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_owner_breakdown_counts_per_user(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    res = await h.fn_automation_owners(_Ctx(ALICE), h.OwnerStatsParams())
    by_user = {row["user_id"]: row for row in res.data["items"]}

    assert by_user[ALICE]["total"] == 2
    assert by_user[ALICE]["active"] == 1
    assert by_user[ALICE]["paused"] == 1
    assert by_user[ALICE]["never_triggered"] == 1
    assert by_user[BOB]["failing"] == 1
    assert by_user[BOB]["total_runs"] == 5
    assert by_user[ALICE]["first_created"] == "2026-08-01T09:00:00Z"


# ─── mutation guards ──────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_non_admin_cannot_pause_another_users_rule(gw, monkeypatch):
    _as_admin(monkeypatch, False)
    res = await h.fn_pause_automation(_Ctx(ALICE), h.RuleIdParams(rule_id=203))
    assert res.status == "error"
    assert gw["patched"] == []          # nothing reached the gateway


@pytest.mark.asyncio
async def test_admin_can_pause_another_users_rule(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    res = await h.fn_pause_automation(_Ctx(ALICE), h.RuleIdParams(rule_id=203))
    assert res.status == "success"
    assert gw["patched"] == [(203, {"status": "paused"})]
    assert res.data["user_id"] == BOB


@pytest.mark.asyncio
async def test_delete_receipt_records_what_was_destroyed(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    res = await h.fn_delete_automation(_Ctx(ALICE), h.RuleIdParams(rule_id=203))
    assert res.status == "success"
    assert res.data["deleted"] is True
    assert res.data["user_id"] == BOB
    assert res.data["deleted_prompt"] == "Nightly backup report"


# ─── bulk safety ──────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_bulk_refuses_unfiltered_run(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    res = await h.fn_bulk_automation_action(
        _Ctx(ALICE), h.BulkRuleParams(operation="delete", dry_run=False),
    )
    assert res.status == "error"
    assert "refusing" in res.error.lower()
    assert gw["deleted"] == []


@pytest.mark.asyncio
async def test_bulk_dry_run_changes_nothing(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    res = await h.fn_bulk_automation_action(
        _Ctx(ALICE), h.BulkRuleParams(operation="delete", never_triggered=True),
    )
    assert res.status == "success"
    assert res.data["dry_run"] is True
    assert res.data["selected"] == 1
    assert gw["deleted"] == []


@pytest.mark.asyncio
async def test_bulk_applies_when_confirmed(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    res = await h.fn_bulk_automation_action(
        _Ctx(ALICE),
        h.BulkRuleParams(operation="delete", never_triggered=True, dry_run=False),
    )
    assert res.status == "success"
    assert res.data["succeeded"] == [102]
    assert gw["deleted"] == [102]


@pytest.mark.asyncio
async def test_bulk_rejects_ids_that_are_not_yours(gw, monkeypatch):
    _as_admin(monkeypatch, False)
    res = await h.fn_bulk_automation_action(
        _Ctx(ALICE),
        h.BulkRuleParams(operation="pause", rule_ids=[101, 203], dry_run=False),
    )
    assert res.status == "error"
    assert "203" in res.error
    assert gw["patched"] == []          # all-or-nothing


@pytest.mark.asyncio
async def test_bulk_rejects_unknown_operation(gw, monkeypatch):
    _as_admin(monkeypatch, True)
    res = await h.fn_bulk_automation_action(
        _Ctx(ALICE), h.BulkRuleParams(operation="explode", rule_ids=[101]),
    )
    assert res.status == "error"
    assert "pause" in res.error
