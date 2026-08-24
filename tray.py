"""Automations · System tray item.

WHY THIS FILE EXISTS
--------------------
The automations counter in the Panel's system tray used to be a hard-coded
React component (`AutomationsBadge`) with a hard-coded entry in the Panel's
tray list and a hard-coded row in the Panel's badge-source table. Three
places in the PANEL had to know that this extension exists and what its
number means.

That is backwards. The tray is a platform surface and `@ext.tray` is its
public contract: the app that owns the number should be the app that
publishes it. Anything else means a first-party app enjoys a privilege no
third-party developer can have, and the contract stays theoretical.

The count is read the same way the skeleton reads it (`list_active_rules`,
filtered to this user) so the tray and the AI context can never disagree.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import ext
from api import list_active_rules

log = logging.getLogger("automations")


@ext.tray(
    "active",
    icon="Bot",
    tooltip="AI Cloud Agents",
    zone="status",
    # Below the platform's 0-99 band on purpose: this item used to BE a
    # built-in at order 20, and keeping that slot means the strip looks
    # identical after the move. A tray that reshuffles itself when an app is
    # re-implemented would be a regression the user can see.
    order=20,
    # How many agents are armed is a figure the user READS at a glance, and
    # the built-in drew it next to the bot glyph. Same shape, now declared
    # through the contract instead of hardcoded in the Panel.
    badge_style="inline",
    # RESTING colour. The built-in bot was NOT muted while nothing was armed:
    # it was violet, always, whatever the count. Declaring "muted" here put
    # the glyph out on every load where no agents happen to be running, and
    # `muted` is dimmer than the strip's own ink rather than equal to it.
    #
    # `primary` is the closest the semantic vocabulary comes to that violet
    # (the token set has no purple, and inventing one for a single item would
    # put this app outside the theme the moment the theme changes). Names,
    # never hex -- so it follows the user's theme instead of fighting it.
    icon_color="primary",
)
async def tray_active_rules(ctx, **kwargs) -> ui.UINode:
    """Green when automations are armed, grey when none are.

    A zero is deliberately still returned rather than suppressed: the Panel
    hides zero-valued badges itself, so returning the real number keeps that
    decision in one place instead of two.
    """
    user_id = ctx.user.imperal_id
    tenant_id = getattr(ctx.user, "tenant_id", "default")

    active = 0
    try:
        rules = await list_active_rules(ctx, tenant_id=tenant_id)
        active = sum(
            1 for r in rules
            if r.get("user_id") == user_id and r.get("status") == "active"
        )
    except Exception as exc:
        # An unreachable rules API must not blank the whole tray: fall through
        # with zero, which renders as a plain icon.
        log.warning("tray: rule fetch failed: %s", exc, exc_info=True)

    # TrayResponse is the canonical envelope: the kernel unpacks `badge` and
    # `panel` and renders each with its own component. Returning a bare Badge
    # would happen to work only if every consumer guessed the same shape --
    # which is exactly the kind of private handshake this contract replaces.
    return ui.TrayResponse(
        badge=ui.Badge(value=active, color="green" if active > 0 else "gray"),
        # The GLYPH follows the same state as the number: green while agents
        # are armed, and back to its resting violet-ish `primary` when none
        # are -- NOT muted. Going muted is what made the strip turn
        # black-and-white for anyone with no automations running, which is a
        # perfectly ordinary state and not one worth greying the icon for.
        icon_color="success" if active > 0 else "primary",
    )
