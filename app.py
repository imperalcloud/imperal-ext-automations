"""Automations · Extension declaration.

Static, side-effect-free. No module-level mutable state, no monkey
patches, no dynamic system-prompt injection. The platform event
catalog flows through the @ext.skeleton tool — LLM sees it via the
per-turn skeleton context with no SDK-internals reach-around.
"""
from __future__ import annotations

import logging
from pathlib import Path

from imperal_sdk import Extension
from imperal_sdk.chat import ChatExtension

log = logging.getLogger("automations")

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.txt").read_text()


# ─── Extension ────────────────────────────────────────────────────────── #

ext = Extension(
    "automations",
    version="1.10.0",
    system=True,  # Imperal-owned platform app (mirrors admin/marketplace) —
    # first-party, hidden from Marketplace search, auto-installed for every
    # user. Was missing here (same latent gap found+fixed on developer-ext
    # and billing-ext, 2026-07-16) even though developer_apps.system was
    # hand-seeded to 1 in the DB; deploy_app now self-heals `system` FROM
    # this manifest field on every deploy, so without this declaration the
    # next automations deploy would have silently flipped the DB flag to 0.
    capabilities=[
        # Rule CRUD
        "automations:read", "automations:write", "automations:delete",
        # Automation runtime subscribes to platform events
        "events:subscribe",
        # Rule state + event catalog persistence
        "store:read", "store:write",
        "config:read", "config:write",
        # LLM-driven rule creation from natural language
        "ai:complete",
        # Namespace umbrella for tool_automations_chat orchestration
        "automations:*",
    ],
    display_name="AI Cloud Agents",
    description=(
        "Event-driven automation rules — subscribe to platform events "
        "(email arrivals, schedule ticks, custom signals) and run multi-step "
        "actions across other extensions when conditions match."
    ),
    icon="icon.svg",
    actions_explicit=True,
)

chat = ChatExtension(
    ext,
    "tool_automations_chat",  # locked — production routing depends on this name
    description=(
        "AI Cloud Agents manager — create, list, pause, resume, delete automation rules. "
        "Automations trigger on events (email received, note created, schedule) and execute actions."
    ),
    system_prompt=SYSTEM_PROMPT,
)


# ─── Lifecycle ────────────────────────────────────────────────────────── #

@ext.health_check
async def health(ctx) -> dict:
    return {"status": "ok", "version": ext.version}


@ext.on_install
async def on_install(ctx) -> None:
    log.info("automations installed for %s", ctx.user.imperal_id)


@ext.on_event("automation.triggered")
async def on_automation_triggered(ctx, event) -> None:
    rule_id = event.get("data", {}).get("rule_id", "?")
    log.info("automation event: rule %s", rule_id)
