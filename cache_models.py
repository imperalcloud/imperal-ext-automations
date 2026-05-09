"""Automations · @ext.cache_model registrations.

Imported BEFORE handlers/skeleton so that the SDK reverse-lookup
(value class → cache key prefix) is populated when ctx.cache.set/get
is called for the first time.
"""
from __future__ import annotations

from app import ext
from models import EventCatalog, UserRoleSnapshot

# Register cache models DIRECTLY (not subclasses) so the SDK
# reverse-lookup in ctx.cache.get_or_fetch matches by class identity.
# Invariant: I-CACHE-MODEL-ON-EXTENSION-INSTANCE.
ext.cache_model("event_catalog")(EventCatalog)
ext.cache_model("user_role")(UserRoleSnapshot)
