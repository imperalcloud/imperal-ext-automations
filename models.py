"""Automations · Pydantic models.

Param models for @chat.function (V17 federal) plus the typed
EventCatalog used by ctx.cache.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from imperal_sdk import sdl

from constants import DEFAULT_COOLDOWN_SECONDS, DEFAULT_MAX_PER_HOUR


# ─── @chat.function param models (V17) ────────────────────────────────── #

class CreateAutomationParams(BaseModel):
    """Create a new automation rule."""
    event_type: str = Field(
        description=(
            "Trigger event from rules.available_events skeleton "
            "(e.g. email.received, notes.created, system.scheduled)"
        ),
    )
    action_description: str = Field(
        description="What to do when triggered, in natural language",
    )
    schedule: str = Field(
        default="",
        description="Cron expression (system.scheduled only)",
    )
    cooldown_seconds: int = Field(
        default=DEFAULT_COOLDOWN_SECONDS,
        description="Min seconds between triggers",
    )
    max_per_hour: int = Field(
        default=DEFAULT_MAX_PER_HOUR,
        description="Max triggers per hour",
    )


class RuleIdParams(BaseModel):
    """Target a specific rule by id."""
    rule_id: int = Field(description="The rule ID")


class ListAutomationsParams(BaseModel):
    """Filter for listing automation rules."""
    status: str | None = Field(
        default=None,
        description="Optional filter: 'active', 'paused', or 'error'. Omit for all.",
    )


# ─── SDL entity (additive — platform reads typed entities) ────────────── #

class AutomationRule(sdl.Entity, sdl.Prioritized, sdl.WorkflowState):
    """One automation rule, as projected by ``handlers._rule_summary``.

    ADDITIVE SDL migration: every existing field is kept verbatim (same
    name AND same type) so the panels / sidebar / list-item builders that
    read ``rule_id`` / ``prompt`` / ``status`` / ``trigger_count`` etc.
    keep working unchanged. The canonical SDL ``id`` / ``title`` / ``kind``
    are populated from the existing fields via a ``mode="before"``
    validator, so existing
    ``ActionResult.success(data={"rules": [_rule_summary(r), ...]})`` calls
    construct this model without any caller-side changes.

    Field → role map:
      id              <- rule_id   (core.id)
      title           <- prompt    (core.title)
      status          <- status    (core.status; active/paused/error)
    The trigger/throttle counters, timestamps and owner are kept as their
    EXISTING string/int shapes and carry custom ``automations.*`` roles —
    they are NOT remapped onto facets whose field types differ (e.g. the
    standard ``time.created_at`` is a ``datetime`` and ``people.owner`` is a
    ``Ref``, which would reject the existing ISO-string / id-string values).
    ``sdl.WorkflowState.state`` mirrors the lifecycle state for state-machine
    aware platform consumers; ``sdl.Prioritized`` is mixed in (all-optional)
    for forward-compat with rule urgency. No existing field is renamed.
    """

    # --- existing fields kept verbatim (panels/sidebar/list rely on them) ---
    rule_id: int | None = None
    prompt: str = ""
    trigger_count: int = sdl.field(default=0, role="automations.trigger_count")
    success_count: int = sdl.field(default=0, role="automations.success_count")
    fail_count: int = sdl.field(default=0, role="automations.fail_count")
    last_error: str | None = None
    cooldown_seconds: int = sdl.field(default=0, role="automations.cooldown_seconds")
    max_per_hour: int = sdl.field(default=0, role="automations.max_per_hour")
    created_at: str = sdl.field(default="", role="automations.created_at")
    user_id: str = sdl.field(default="", role="automations.user_id")

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            rid = data.get("rule_id")
            data.setdefault("id", rid if rid is not None else "")
            data.setdefault("title", data.get("prompt") or (str(rid) if rid is not None else ""))
            # status is an existing field that maps to core.status verbatim.
            # Mirror the lifecycle status into the WorkflowState.state role too.
            if data.get("status"):
                data.setdefault("state", data["status"])
        return data


# ─── ctx.cache typed model ────────────────────────────────────────────── #

class CatalogEntry(BaseModel):
    """One event in the platform catalog."""
    event_type: str
    description: str = ""
    app_id: str = ""


class EventCatalog(BaseModel):
    """Platform event catalog snapshot, cached via ctx.cache."""
    entries: list[CatalogEntry] = Field(default_factory=list)

    @property
    def valid_event_types(self) -> set[str]:
        return {e.event_type for e in self.entries if e.event_type}


class UserRoleSnapshot(BaseModel):
    """Authoritative user role from auth-gw, cached via ctx.cache.

    Workaround for kernel-side ctx.user.role unreliability — at the
    panel/handler boundary we hit auth-gw `/v1/users/{uid}` directly
    and cache the result for a minute. (See I-FIRSTPARTY-ADMIN-VIEW.)
    """
    role: str = ""
