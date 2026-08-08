"""Automations · Pydantic models.

Param models for @chat.function (V17 federal) plus the typed
EventCatalog used by ctx.cache.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from imperal_sdk import sdl

from constants import DEFAULT_COOLDOWN_SECONDS


# ─── @chat.function param models (V17) ────────────────────────────────── #

class StructuredAction(BaseModel):
    """WS2 grounded action — the resolved (app_id, tool, args) the rule runs.
    Optional on create: when present the GW persists it instead of free text.

    For `conn-ssh` (the user's own servers) this is more than an optimisation:
    the stored action IS the pre-authorization. A write-tier server tool
    (run_command / write_file / edit_file) runs unattended ONLY through a stored
    action, because then the command is the literal one the user approved and
    can re-read in the panel. Described in words instead, it cannot run.
    """
    app_id: str = Field(description="Extension that owns the tool, from the user's live scope (or 'conn-ssh' for the user's servers)")
    tool: str = Field(description="@chat.function name the user can actually invoke")
    args: dict = Field(default_factory=dict, description="Well-formed args for the tool (conn-ssh: connection_id + e.g. command)")


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
    action: Optional[StructuredAction] = Field(
        default=None,
        description="Resolved (app_id, tool, args) for grounded persistence (WS2). Omit to keep NL action.",
    )
    notify_mode: str = Field(
        default="all",
        description="Run notifications (bell + chat): 'all' (default) / 'failures' / 'off'.",
    )


class RuleIdParams(BaseModel):
    """Target a specific rule by id."""
    rule_id: int = Field(description="The rule ID")


class ListAutomationsParams(BaseModel):
    """Filter for listing automation rules.

    Every filter is optional and they COMBINE (logical AND). Admins may
    additionally scope by owner; for non-admins the owner filters are
    ignored (they only ever see their own rules).
    """
    status: str | None = Field(
        default=None,
        description="Optional filter: 'active', 'paused', or 'error'. Omit for all.",
    )
    mine: bool = Field(
        default=False,
        description=(
            "Show ONLY the calling user's own rules. Set this whenever the "
            "user says 'my automations' / 'мои автоматизации' / 'the ones I "
            "created'. The owner id is taken from the authenticated session, "
            "so it is always correct — NEVER try to guess the caller's "
            "imperal_id and pass it via user_id instead. Admins included: an "
            "admin asking for THEIR rules wants this, not the whole tenant."
        ),
    )
    user_id: str = Field(
        default="",
        description=(
            "ADMIN ONLY — show rules belonging to SOMEBODY ELSE. Accepts a "
            "full imperal_id (imp_u_...) or a fragment of it. Use when asked "
            "'which automations belong to <user>'. For the caller's OWN rules "
            "use mine=true instead."
        ),
    )
    event_type: str = Field(
        default="",
        description=(
            "Filter by trigger event, e.g. 'system.scheduled', 'email.received'. "
            "Substring match, so 'email' matches every email.* trigger."
        ),
    )
    search: str = Field(
        default="",
        description=(
            "Free-text search across prompt, interpretation, action text and "
            "last_error (case-insensitive)."
        ),
    )
    scheduled_only: bool = Field(
        default=False,
        description="Only cron/scheduled rules (those carrying a cron schedule).",
    )
    failing_only: bool = Field(
        default=False,
        description="Only rules that currently carry a last_error or have failures.",
    )
    never_triggered: bool = Field(
        default=False,
        description=(
            "Only rules that have NEVER fired (trigger_count=0). Useful for "
            "spotting dead or orphaned automations."
        ),
    )
    created_after: str = Field(
        default="",
        description="Only rules created at/after this ISO date, e.g. '2026-08-01'.",
    )
    created_before: str = Field(
        default="",
        description="Only rules created at/before this ISO date, e.g. '2026-08-31'.",
    )
    sort: str = Field(
        default="",
        description=(
            "Sort order: 'newest' (default), 'oldest', 'most_triggered', "
            "'most_failed', 'owner'."
        ),
    )
    limit: int = Field(
        default=200,
        description="Max rules to return (1-500). Applied AFTER filtering and sorting.",
    )


class UpdateAutomationParams(BaseModel):
    """Edit an existing automation rule in place (preserves rule_id + stats)."""
    rule_id: int = Field(description="The rule ID to edit")
    action_description: str | None = Field(default=None, description="New plain-language action (re-grounded)")
    event_type: str | None = Field(default=None, description="New trigger event (must exist in the user's catalog)")
    schedule: str | None = Field(default=None, description="New cron expression (system.scheduled only)")
    cooldown_seconds: int | None = Field(default=None, description="New min seconds between triggers")
    status: str | None = Field(default=None, description="New status: 'active' or 'paused'")
    action: Optional[StructuredAction] = Field(
        default=None,
        description="New resolved (app_id, tool, args) grounded action (WS2). Re-grounded by the GW. Omit to keep the existing action or use action_description for NL.",
    )
    notify_mode: str | None = Field(
        default=None,
        description="Change run notifications (bell + chat): 'all' / 'failures' / 'off'. Omit to keep current.",
    )


class RuleDetailsParams(BaseModel):
    """Target one rule for a full, nothing-held-back detail read."""
    rule_id: int = Field(description="The rule ID")
    include_schedule_health: bool = Field(
        default=True,
        description=(
            "Also report derived health: whether the rule is a cron rule, "
            "whether it has ever fired, its success ratio and failure state."
        ),
    )


class BulkRuleParams(BaseModel):
    """Apply one lifecycle operation to SEVERAL rules in a single call.

    Either pass explicit ``rule_ids``, or select rules with the same
    filters ``list_automations`` accepts. Selection filters are the safe
    way to act on 'every failing rule of user X' without enumerating ids
    by hand. Admins may target other users' rules; non-admins are always
    confined to their own.
    """
    rule_ids: list[int] = Field(
        default_factory=list,
        description="Explicit rule IDs to act on. Omit to select by filter instead.",
    )
    operation: str = Field(
        description="What to do: 'pause', 'resume', or 'delete'.",
    )
    user_id: str = Field(
        default="",
        description="ADMIN ONLY — restrict the selection to this owner (id or fragment).",
    )
    status: str = Field(
        default="",
        description="Restrict selection to rules in this status ('active'/'paused'/'error').",
    )
    event_type: str = Field(
        default="",
        description="Restrict selection to this trigger event (substring match).",
    )
    search: str = Field(
        default="",
        description="Restrict selection to rules whose text matches this term.",
    )
    never_triggered: bool = Field(
        default=False,
        description="Restrict selection to rules that have never fired.",
    )
    failing_only: bool = Field(
        default=False,
        description="Restrict selection to rules that currently carry a failure.",
    )
    dry_run: bool = Field(
        default=True,
        description=(
            "Preview only — report exactly which rules WOULD be affected and "
            "change nothing. Defaults to TRUE: a bulk lifecycle operation "
            "never fires by accident. Set false (with confirm=true) to apply."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Required for 'delete'. Without it a delete is refused and only "
            "reports what WOULD be deleted (safe dry run)."
        ),
    )


class OwnerStatsParams(BaseModel):
    """Group every automation rule by owner (admin oversight)."""
    user_id: str = Field(
        default="",
        description="Optional — report on a single owner (id or fragment) instead of all.",
    )
    include_rule_ids: bool = Field(
        default=True,
        description="Include each owner's rule IDs in the breakdown.",
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
    created_at: str = sdl.field(default="", role="automations.created_at")
    user_id: str = sdl.field(default="", role="automations.user_id")
    notify_mode: str = sdl.field(default="all", role="automations.notify_mode")
    trigger_filter: dict = Field(default_factory=dict)
    actions: list[dict] = Field(default_factory=list)
    interpretation: str = ""
    last_triggered: str = ""

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
    caller_user_id: str = ""
    total_matched: int = 0
    truncated: bool = False
    filter: dict = Field(default_factory=dict)


class BulkActionReceipt(sdl.Entity):
    """Receipt for ``bulk_automation_action`` — what was selected, what
    actually happened, and what failed, per rule id.

    Field names mirror the handler's return dict verbatim
    (I-EXT-RECORD-FIELD-NAMING-SYMMETRIC).
    """
    operation: str = ""
    selected: int = 0
    succeeded: list[int] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list)
    dry_run: bool = False
    admin_view: bool = False

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            op = data.get("operation") or "bulk"
            data.setdefault("id", op)
            n = data.get("selected", 0)
            data.setdefault("title", f"{op}: {n} rule(s)")
            data.setdefault("kind", "receipt")
        return data


class OwnerRuleStats(sdl.Entity):
    """Per-owner automation statistics (admin oversight)."""
    user_id: str = sdl.field(default="", role="automations.user_id")
    total: int = 0
    active: int = 0
    paused: int = 0
    failing: int = 0
    never_triggered: int = 0
    scheduled: int = 0
    total_runs: int = 0
    total_failures: int = 0
    first_created: str = ""
    last_created: str = ""
    rule_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            uid = data.get("user_id") or ""
            data.setdefault("id", uid)
            data.setdefault("title", uid or "(unknown owner)")
            data.setdefault("kind", "owner")
        return data


class OwnerStatsResponse(sdl.EntityList[OwnerRuleStats]):
    """``automation_owners`` return shape — one row per owner."""
    total_owners: int = 0
    total_rules: int = 0
    admin_view: bool = False


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


class CapabilityEntry(BaseModel):
    """One invokable @chat.function tool in the platform capability catalog."""
    app_id: str = ""
    tool: str = ""
    action_type: str = "read"
    required_params: list[str] = Field(default_factory=list)
    optional_params: list[str] = Field(default_factory=list)


class CapabilityCatalog(BaseModel):
    """Platform capability catalog snapshot (per-app tools + param names),
    cached via ctx.cache. Lets the producer emit a grounded StructuredAction
    (app_id, tool, args) instead of an opaque free-text message."""
    entries: list[CapabilityEntry] = Field(default_factory=list)


class CapabilityPageIndex(BaseModel):
    """Page count for the PAGED capability-catalog cache (live 2026-07-12:
    the catalog outgrew the single 64KB cache entry — 83,460 bytes — and the
    oversized write raised out of get_or_fetch, blanking the whole catalog).
    Pages live under `{CAPABILITY_CACHE_KEY}:p{i}`; this index under
    `{CAPABILITY_CACHE_KEY}:idx`."""
    pages: int = 0


class EventCatalogPageIndex(BaseModel):
    """Page count for the PAGED event-catalog cache (live 2026-07-13: the
    event catalog outgrew the 64KB entry too — 86,870 bytes / 326 events —
    and get_or_fetch raised out of the WRITE after a successful fetch,
    blanking available_events every skeleton refresh). Pages live under
    `{CATALOG_CACHE_KEY}:p{i}`; this index under `{CATALOG_CACHE_KEY}:idx`."""
    pages: int = 0


class UserRoleSnapshot(BaseModel):
    """Authoritative user role from auth-gw, cached via ctx.cache.

    Workaround for kernel-side ctx.user.role unreliability — at the
    panel/handler boundary we hit auth-gw `/v1/users/{uid}` directly
    and cache the result for a minute. (See I-FIRSTPARTY-ADMIN-VIEW.)
    """
    role: str = ""
