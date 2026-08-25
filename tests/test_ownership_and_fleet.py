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
    # action_summary is the SAME human sentence the panel renders
    # (shared via action_text.describe_actions), not a raw app.tool pair.
    assert "run_command" in s["action_summary"]


def test_never_triggered_and_failing_are_derived():
    assert h._rule_summary(RULES[1])["never_triggered"] is True
    assert h._rule_summary(RULES[2])["is_failing"] is True


def test_a_lifetime_failure_does_not_mark_a_healthy_rule_as_failing():
    """`is_failing` is about NOW, not about ever.

    Observed 2026-08-25 on rule 999997: 366 successes, one failure the previous
    evening, last_error empty, most recent run green -- and the chat view still
    reported `is_failing: true`, because the old expression was
    `bool(last_error) or fail_count > 0` over a LIFETIME counter. Every rule
    that ever hiccuped was branded broken forever, so the flag carried no
    information and a genuinely broken rule could not be told apart from a
    healthy one.

    RULES[0] is exactly that shape: 10 successes, 2 old failures, no live
    error. It is healthy.
    """
    assert h._rule_summary(RULES[0])["is_failing"] is False


def test_an_unrecovered_error_still_counts_as_failing():
    """The other half: a live last_error is never explained away.

    RULES[2] carries "smtp timeout" with no later success proving recovery --
    the partial-run trap that painted rule 999778 a calm green for 15 hours.
    It must stay flagged, so the fix above cannot decay into "trust the
    counters".
    """
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
    # 101 has 2 failures somewhere in its LIFETIME, 10 successes and an EMPTY
    # last_error -- it is not failing NOW, so it is no longer listed. 203
    # carries a live unrecovered last_error ("smtp timeout"). This expectation
    # used to be {101, 203}, which pinned the very bug being fixed: one
    # failure back in July branded a rule broken forever.
    (dict(failing_only=True),           {203}),
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


# ─── "show me MY automations" (the production regression) ─────────────── #
#
# An admin sees the whole tenant by default. Asking for THEIR OWN rules used
# to require the caller to know its own imperal_id and pass it as user_id --
# so a wrong guess either hid real rules or blamed a non-existent "identifier
# resolution problem". mine=True takes the id from the session instead.

@pytest.mark.asyncio
async def test_admin_asking_for_mine_gets_only_their_own(gw, monkeypatch):
    """The exact panel question: admin asks for 'my automations'."""
    async def _admin(ctx):
        return True
    monkeypatch.setattr(h, "_is_admin", _admin)

    ctx = _Ctx(ALICE)
    res = await h.fn_list_automations(ctx, h.ListAutomationsParams(mine=True))
    assert res.status == "success"
    owners = {i["user_id"] for i in res.data["items"]}
    assert owners == {ALICE}, "mine=True must never return another user's rules"
    assert all(i["owner_is_caller"] for i in res.data["items"])
    # ...and it says "You have", not "System has".
    assert res.summary.startswith("You have")


@pytest.mark.asyncio
async def test_admin_without_mine_still_sees_the_whole_tenant(gw, monkeypatch):
    """mine=True must NARROW the view, not become the new default."""
    async def _admin(ctx):
        return True
    monkeypatch.setattr(h, "_is_admin", _admin)

    ctx = _Ctx(ALICE)
    res = await h.fn_list_automations(ctx, h.ListAutomationsParams())
    owners = {i["user_id"] for i in res.data["items"]}
    assert len(owners) > 1, "admin default view is the whole tenant"
    assert res.data["admin_view"] is True


@pytest.mark.asyncio
async def test_response_carries_the_caller_id_so_nobody_has_to_guess(gw, monkeypatch):
    """caller_user_id is what removes the guessing from 'which are mine'."""
    async def _admin(ctx):
        return True
    monkeypatch.setattr(h, "_is_admin", _admin)

    ctx = _Ctx(BOB)
    res = await h.fn_list_automations(ctx, h.ListAutomationsParams())
    assert res.data["caller_user_id"] == BOB
    mine = [i for i in res.data["items"] if i["owner_is_caller"]]
    assert {i["user_id"] for i in mine} == {BOB}


@pytest.mark.asyncio
async def test_mine_beats_a_wrongly_guessed_user_id(gw, monkeypatch):
    """If the model still guesses an id, mine=True must win over it."""
    async def _admin(ctx):
        return True
    monkeypatch.setattr(h, "_is_admin", _admin)

    ctx = _Ctx(ALICE)
    res = await h.fn_list_automations(
        ctx, h.ListAutomationsParams(mine=True, user_id=BOB)
    )
    owners = {i["user_id"] for i in res.data["items"]}
    assert owners == {ALICE}, "session identity must override a guessed id"


@pytest.mark.asyncio
async def test_mine_with_no_rules_is_an_empty_list_not_an_error(gw, monkeypatch):
    """A caller with zero rules gets an honest empty answer.

    This is the other half of the production failure: an empty result must
    read as 'you have none', never as a broken id mapping.
    """
    async def _admin(ctx):
        return True
    monkeypatch.setattr(h, "_is_admin", _admin)

    ctx = _Ctx("imp_u_nobody00000")
    res = await h.fn_list_automations(ctx, h.ListAutomationsParams(mine=True))
    assert res.status == "success"
    assert res.data["items"] == []
    assert res.data["caller_user_id"] == "imp_u_nobody00000"


# ─── owner identity must never be an email ────────────────────────────── #
#
# Reported from the panel: the owner column showed an unknown "@" identity
# that then vanished. Emails are redacted to a placeholder before display and
# the panel's HTML swallows the angle brackets, leaving a blank. The rules'
# own text legitimately CONTAINS emails (rule 44 replies to one), so the fix
# is to hand the narrator a ready-made owner caption it never has to invent.

@pytest.mark.asyncio
async def test_owner_label_is_an_imperal_id_never_an_email(gw, monkeypatch):
    async def _admin(ctx):
        return True
    monkeypatch.setattr(h, "_is_admin", _admin)

    res = await h.fn_list_automations(_Ctx(ALICE), h.ListAutomationsParams())
    assert res.status == "success"
    for it in res.data["items"]:
        label = it["owner_label"]
        assert "@" not in label, f"owner_label must never be an email: {label!r}"
        assert label == "you" or label.startswith("imp_u_"), label
        # caller's own rules read as "you", never as a raw id echoed back
        assert (label == "you") == it["owner_is_caller"]


@pytest.mark.asyncio
async def test_rule_text_may_contain_an_email_without_it_becoming_the_owner(gw, monkeypatch):
    """A rule that emails somebody must not borrow that address as its owner."""
    async def _admin(ctx):
        return True
    monkeypatch.setattr(h, "_is_admin", _admin)

    emailish = {
        "id": 44, "user_id": ALICE,
        "prompt": "When I receive an email from someone@example.com, reply",
        "status": "paused", "trigger_count": 24, "success_count": 6,
        "fail_count": 0, "last_error": None, "created_at": "2026-04-08T03:50:41Z",
        "trigger_filter": {"event_type": "email.received",
                           "conditions": {"from_contains": "someone@example.com"}},
        "actions": [{"message": "reply to someone@example.com"}],
    }
    gw["rules"].append(emailish)

    res = await h.fn_list_automations(_Ctx(ALICE), h.ListAutomationsParams(mine=True))
    row = next(i for i in res.data["items"] if i["rule_id"] == 44)
    assert row["owner_label"] == "you"
    assert row["user_id"] == ALICE
    # the email survives where it BELONGS -- in the rule's own content
    assert "someone@example.com" in str(row["trigger_filter"])


def test_rule_entity_keeps_every_detail_field():
    """Guard against silently dropping fields when editing the model."""
    from models import AutomationRule
    for field in ("user_id", "owner_label", "owner_is_caller", "notify_mode",
                  "trigger_filter", "actions", "interpretation",
                  "last_triggered", "created_at", "last_error"):
        assert field in AutomationRule.model_fields, f"lost field: {field}"
