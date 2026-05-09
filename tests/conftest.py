"""Pytest fixtures for automations smoke tests."""
from __future__ import annotations

import os
import sys

import pytest

# Make the extension package importable when tests are run from the
# project root via `pytest tests/`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imperal_sdk.testing import MockContext


@pytest.fixture
def ctx():
    return MockContext(user_id="imp_u_test_user_001", tenant_id="default")
