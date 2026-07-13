"""Automations · @ext.cache_model registrations.

Imported BEFORE handlers/skeleton so that the SDK reverse-lookup
(value class → cache key prefix) is populated when ctx.cache.set/get
is called for the first time.
"""
from __future__ import annotations

from app import ext
from models import (
    CapabilityCatalog, CapabilityPageIndex, EventCatalog, UserRoleSnapshot,
)

# Register cache models DIRECTLY (not subclasses) so the SDK
# reverse-lookup in ctx.cache.get_or_fetch matches by class identity.
# Invariant: I-CACHE-MODEL-ON-EXTENSION-INSTANCE.
ext.cache_model("event_catalog")(EventCatalog)
ext.cache_model("user_role")(UserRoleSnapshot)
# CapabilityCatalog backs load_capability_catalog_cached (api.py) -> the
# skeleton's available_tools (grounding for create_automation). Was missing
# -> every skeleton refresh raised "CapabilityCatalog is not registered" and
# available_tools silently fell back to empty. Register it like the others.
ext.cache_model("capability_catalog")(CapabilityCatalog)
# CapabilityPageIndex is the :idx entry of the PAGED capability cache
# (api.py, live 2026-07-12). It shipped unregistered -> the :idx write raised
# -> every read fell back to a fresh fetch (cache effectively dead).
ext.cache_model("capability_page_index")(CapabilityPageIndex)
