"""Automations · Module-level constants (no scattered magic numbers)."""
from __future__ import annotations

# Display truncation
PROMPT_TRUNCATE_LEN      = 80   # Rule prompt shown in panel/skeleton/summary
ACTION_DESC_TRUNCATE_LEN = 200  # Stored on rule.interpretation
EVENT_DESC_TRUNCATE_LEN  = 100  # Per-event description in catalog skeleton
OWNER_PREFIX_LEN         = 16   # Truncated user_id shown to admins

# Skeleton shape
SKELETON_RULE_LIMIT = 5  # Max rules summarized in skeleton.rules_summary

# Rule defaults (mirrored on Auth GW side, here for Pydantic Field defaults)
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_MAX_PER_HOUR     = 10

# ctx.cache key + TTL for the platform event catalog
CATALOG_CACHE_KEY         = "event_catalog"
CATALOG_CACHE_TTL_SECONDS = 300

# HTTP transport
HTTP_TIMEOUT_SECONDS = 15
