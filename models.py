"""Automations · Pydantic models.

Param models for @chat.function (V17 federal) plus the typed
EventCatalog used by ctx.cache.
"""
from __future__ import annotations

from typing import Any, Optional

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
            # Two producing shapes feed this entity:
            #   * list_automations -> _rule_summary(r) keyed on ``rule_id``
            #   * get_automation_details -> the RAW gateway rule dict keyed on ``id``
            # Resolve the canonical SDL id from whichever is present, and mirror it
            # back into ``rule_id`` so the panels/list-item builders (which read
            # ``rule_id``) keep working on a details-shaped entity too.
            rid = data.get("rule_id")
            if rid is None:
                rid = data.get("id")
            data["id"] = rid if rid is not None else ""
            if data.get("rule_id") is None and rid is not None:
                data["rule_id"] = rid
            data.setdefault("title", data.get("prompt") or (str(rid) if rid is not None else ""))
            # status is an existing field that maps to core.status verbatim.
            # Mirror the lifecycle status into the WorkflowState.state role too.
            if data.get("status"):
                data.setdefault("state", data["status"])
        return data


class AutomationListResponse(sdl.EntityList[AutomationRule]):
    """``list_automations`` return shape — a REAL ``sdl.EntityList[AutomationRule]``
    (``items=[...]``, ``x-sdl="entity-list"``). The legacy ``{rules:[dict], ...}``
    wrapper is GONE; the handler now returns
    ``data={"items":[...], "total": n, "admin_view": bool, "filter": {...}}``.

    ``admin_view`` and ``filter`` are kept as additive typed fields on the
    EntityList so the existing scalars survive verbatim for the narrator.
    """
    admin_view: bool = False
    filter: dict = Field(default_factory=dict)


class AutomationActionReceipt(sdl.Entity):
    """Receipt entity for the write/destructive rule verbs that return a small
    payload keyed by ``rule_id`` (create / pause / resume / delete) — kind='rule'.

    Field names mirror the ACTUAL handler return-dict keys verbatim
    (I-EXT-RECORD-FIELD-NAMING-SYMMETRIC):
      * create_automation -> {"rule_id", "rule"}
      * pause_automation  -> {"rule_id", "status"}
      * resume_automation -> {"rule_id", "status", "trigger_count_reset"}
      * delete_automation -> {"rule_id", "deleted"}
    One field-symmetric receipt covers all four (reuse, do not invent per-verb
    receipts). The canonical id is the rule_id; the title is drawn from the
    embedded rule's prompt/name where present (create echoes the full rule).
    """
    rule_id: Optional[Any] = None
    rule: Optional[Any] = None
    status: Optional[str] = None
    deleted: Optional[bool] = None
    trigger_count_reset: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            _rule = data.get("rule")
            inner = _rule if isinstance(_rule, dict) else {}
            rid = data.get("rule_id")
            if rid is None:
                rid = inner.get("id")
            data["id"] = rid if rid is not None else ""
            data.setdefault(
                "title",
                inner.get("prompt") or inner.get("name")
                or (str(rid) if rid is not None else ""),
            )
            data.setdefault("kind", "rule")
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
