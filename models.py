"""Automations · Pydantic models.

Param models for @chat.function (V17 federal) plus the typed
EventCatalog used by ctx.cache.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

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
