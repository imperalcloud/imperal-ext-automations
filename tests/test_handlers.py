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
    DEFAULT_MAX_PER_HOUR,
    PROMPT_TRUNCATE_LEN,
    SKELETON_RULE_LIMIT,
)
from models import (
    CatalogEntry,
    CreateAutomationParams,
    EventCatalog,
    RuleIdParams,
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
        assert p.max_per_hour == DEFAULT_MAX_PER_HOUR
        assert p.schedule == ""

    def test_accepts_explicit_overrides(self):
        p = CreateAutomationParams(
            event_type="system.scheduled",
            action_description="Daily digest.",
            schedule="0 9 * * *",
            cooldown_seconds=600,
            max_per_hour=2,
        )
        assert p.schedule == "0 9 * * *"
        assert p.cooldown_seconds == 600
        assert p.max_per_hour == 2


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
    assert DEFAULT_MAX_PER_HOUR > 0
