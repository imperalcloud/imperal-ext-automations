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

# Cap on the event-type dropdown in the workshop — I7.
#
# Measured live on production: the catalog had 667 events and rendered ONE
# Select of 127.6KB -- 90% of the whole workshop reply (141.7KB), with the
# rule table costing only 11.5KB. At ~196B per option the panel would have
# hit the kernel's 256KB cap at ~1264 events and vanished exactly the way
# the sidebar did.
#
# Unlike rule counts, this grows with how many APPS are installed platform-
# wide, so it climbs on its own without the user doing anything. A 667-entry
# dropdown is also unusable by hand -- this is a usability bound first and a
# size bound second. Users can still target any event by describing it in
# words; the rule prompt is the real interface.
EVENT_OPTIONS_MAX = 150

# Cap on the workshop's outcomes table — I7.
#
# A row is cheap (~650B) but unbounded is still unbounded: the table shares
# one reply with the event dropdown and the rule editor, and the table alone
# breached the 256KB cap at ~1000 rules. Failing rules are rendered first, so
# the cap only ever costs healthy rows -- the ones this table is not for.
OUTCOME_ROWS_MAX = 200

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
