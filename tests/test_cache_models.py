"""Regression: every Pydantic model read/written via ctx.cache.get_or_fetch
MUST be registered with @ext.cache_model, or the SDK raises
"<Model> is not registered" (I-CACHE-MODEL-REGISTRATION-REQUIRED) at runtime
and the read silently fails.

CapabilityCatalog was missing from cache_models.py while api.load_capability_catalog_cached
reads it -> every skeleton refresh logged "CapabilityCatalog is not registered"
and the skeleton's available_tools fell back to empty (breaking grounding for
create_automation). 2026-06-19.
"""
import app  # noqa: F401  (ext instance)
import cache_models  # noqa: F401  (import triggers the registrations)
from models import EventCatalog, UserRoleSnapshot, CapabilityCatalog


def test_all_cache_models_registered():
    ext = app.ext
    assert ext._resolve_cache_model_name(EventCatalog) == "event_catalog"
    assert ext._resolve_cache_model_name(UserRoleSnapshot) == "user_role"
    # the one that was missing:
    assert ext._resolve_cache_model_name(CapabilityCatalog) == "capability_catalog"
