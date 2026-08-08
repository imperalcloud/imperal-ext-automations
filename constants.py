"""Automations · Module-level constants (no scattered magic numbers)."""
from __future__ import annotations

# Display truncation
PROMPT_TRUNCATE_LEN      = 80   # Rule prompt shown in panel/skeleton/summary
ACTION_DESC_TRUNCATE_LEN = 200  # Stored on rule.interpretation
EVENT_DESC_TRUNCATE_LEN  = 100  # Per-event description in catalog skeleton
OWNER_PREFIX_LEN         = 16   # Truncated user_id shown to admins

# Skeleton shape
SKELETON_RULE_LIMIT = 5  # Max rules summarized in skeleton.rules_summary

# Sidebar reply budget — I7.
#
# The kernel caps a fast-RPC reply at 256KB (REPLY_PAYLOAD_MAX_BYTES in
# imperal_kernel/rpc/stream_consumer.py). Over that, the reply is not
# trimmed: _publish_reply REPLACES it with a typed APPLICATION error
# ("reply truncated: payload exceeds 256KB cap"), so the panel returns NO
# ui at all. The frontend then marks the slot missing and renders nothing
# for it -- not even a spinner (ExtensionPage: `configHasLeft && !missing.left`)
# -- so the left panel simply DISAPPEARS.
#
# An admin sees every rule in the tenant, and a rule list item costs ~3.5KB
# on the wire, so the sidebar crossed the cap at ~70 rules and vanished.
# This budget bounds the rendered list so the reply stays well inside the
# cap at ANY rule count; the remainder is reported honestly in the panel.
SIDEBAR_ITEM_BUDGET_BYTES = 170_000

# Rule defaults (mirrored on Auth GW side, here for Pydantic Field defaults)
DEFAULT_COOLDOWN_SECONDS = 60

# ctx.cache key + TTL for the platform event catalog
CATALOG_CACHE_KEY         = "event_catalog"
CATALOG_CACHE_TTL_SECONDS = 300

# ctx.cache key for the platform capability catalog (per-app invokable tools
# + param names) — published by the kernel alongside the event catalog. Reuses
# CATALOG_CACHE_TTL_SECONDS. Lets the producer LLM ground a structured action.
CAPABILITY_CACHE_KEY = "capability_catalog"

# ctx.cache key + TTL for authoritative user-role lookup (per-user namespace)
USER_ROLE_CACHE_KEY         = "user_role"
USER_ROLE_CACHE_TTL_SECONDS = 60

# HTTP transport
HTTP_TIMEOUT_SECONDS = 15
