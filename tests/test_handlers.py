"""Smoke tests for the automations extension.

These tests cover the parts that don't require a live Auth GW —
Pydantic param validation (V17 federal), constants integrity, and
the typed cache model. Live HTTP path is exercised by integration
tests on staging, not here.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from constants import (
    DEFAULT_COOLDOWN_SECONDS,
    PROMPT_TRUNCATE_LEN,
    SKELETON_RULE_LIMIT,
)
from models import (
    CatalogEntry,
    CreateAutomationParams,
    EventCatalog,
    RuleIdParams,
    UpdateAutomationParams,
)


# ─── Pydantic param contract (V17/V18 federal) ────────────────────────── #

class TestCreateAutomationParams:
    def test_rejects_missing_required(self):
        with pytest.raises(ValidationError):
            CreateAutomationParams()  # type: ignore[call-arg]

    def test_accepts_minimum_required(self):
        p = CreateAutomationParams(
            event_type="email.received",
            action_description="Forward important mail to Slack.",
        )
        assert p.event_type == "email.received"
        assert p.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS
        assert p.schedule == ""

    def test_accepts_explicit_overrides(self):
        p = CreateAutomationParams(
            event_type="system.scheduled",
            action_description="Daily digest.",
            schedule="0 9 * * *",
            cooldown_seconds=600,
        )
        assert p.schedule == "0 9 * * *"
        assert p.cooldown_seconds == 600


class TestRuleIdParams:
    def test_accepts_int(self):
        assert RuleIdParams(rule_id=42).rule_id == 42

    def test_rejects_non_numeric(self):
        with pytest.raises(ValidationError):
            RuleIdParams(rule_id="not-an-int")  # type: ignore[arg-type]


# ─── EventCatalog typed cache model ───────────────────────────────────── #

class TestEventCatalog:
    def test_empty_default(self):
        cat = EventCatalog()
        assert cat.entries == []
        assert cat.valid_event_types == set()

    def test_collects_unique_event_types(self):
        cat = EventCatalog(entries=[
            CatalogEntry(event_type="email.received", description="d1"),
            CatalogEntry(event_type="notes.created",  description="d2"),
            CatalogEntry(event_type="email.received", description="dup"),
        ])
        assert cat.valid_event_types == {"email.received", "notes.created"}

    def test_skips_blank_event_types(self):
        cat = EventCatalog(entries=[
            CatalogEntry(event_type="email.received"),
            CatalogEntry(event_type=""),
        ])
        assert cat.valid_event_types == {"email.received"}


# ─── Constants sanity ─────────────────────────────────────────────────── #

def test_truncation_limits_are_positive():
    """Magic-number-ectomy invariants: every truncation is >0."""
    assert PROMPT_TRUNCATE_LEN > 0
    assert SKELETON_RULE_LIMIT > 0
    assert DEFAULT_COOLDOWN_SECONDS > 0


# ─── UpdateAutomationParams ───────────────────────────────────────────── #

class TestUpdateAutomationParams:
    def test_requires_rule_id(self):
        with pytest.raises(ValidationError):
            UpdateAutomationParams()  # type: ignore[call-arg]

    def test_accepts_rule_id_only(self):
        p = UpdateAutomationParams(rule_id=228)
        assert p.rule_id == 228
        assert p.action_description is None
        assert p.event_type is None
        assert p.schedule is None
        assert p.cooldown_seconds is None
        assert p.status is None

    def test_accepts_partial_edit(self):
        p = UpdateAutomationParams(rule_id=1, action_description="Summarize new notes", cooldown_seconds=300)
        assert p.action_description == "Summarize new notes"
        assert p.cooldown_seconds == 300
        assert p.event_type is None

    def test_rejects_non_numeric_rule_id(self):
        with pytest.raises(ValidationError):
            UpdateAutomationParams(rule_id="x")  # type: ignore[arg-type]


# ─── fn_update_automation handler tests ──────────────────────────────────── #

@pytest.mark.asyncio
async def test_update_rejects_unknown_event_type_before_gw(ctx, monkeypatch):
    async def _fake_catalog(_ctx):
        return EventCatalog(entries=[CatalogEntry(event_type="email.received", description="d")])
    monkeypatch.setattr("handlers.load_event_catalog_cached", _fake_catalog)
    called = {"patched": False}
    async def _fake_patch(_ctx, _rid, _patch):
        called["patched"] = True
        return True
    monkeypatch.setattr("handlers.patch_rule", _fake_patch)
    from handlers import fn_update_automation
    res = await fn_update_automation(ctx, UpdateAutomationParams(rule_id=1, event_type="made.up.event"))
    assert res.status == "error"
    assert "not found" in (res.error or "").lower()
    assert called["patched"] is False


@pytest.mark.asyncio
async def test_update_requires_cron_on_scheduled_edit(ctx, monkeypatch):
    async def _fake_catalog(_ctx):
        return EventCatalog(entries=[CatalogEntry(event_type="system.scheduled", description="cron")])
    monkeypatch.setattr("handlers.load_event_catalog_cached", _fake_catalog)
    from handlers import fn_update_automation
    res = await fn_update_automation(ctx, UpdateAutomationParams(rule_id=1, event_type="system.scheduled"))
    assert res.status == "error"
    assert "cron" in (res.error or "").lower() or "schedule" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_update_patches_cooldown_via_gw(ctx, monkeypatch):
    captured = {}
    async def _fake_patch(_ctx, rid, patch):
        captured["rid"] = rid
        captured["patch"] = patch
        return True
    monkeypatch.setattr("handlers.patch_rule", _fake_patch)
    from handlers import fn_update_automation
    res = await fn_update_automation(ctx, UpdateAutomationParams(rule_id=42, cooldown_seconds=600))
    assert res.status == "success"
    assert captured["rid"] == 42
    assert captured["patch"] == {"cooldown_seconds": 600}
