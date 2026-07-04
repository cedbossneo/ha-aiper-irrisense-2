"""Shared pytest fixtures for the Aiper Irrisense 2 tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the custom integration in every test.

    `enable_custom_integrations` is provided by
    pytest-homeassistant-custom-component; making it autouse means individual
    tests don't have to request it explicitly.
    """
    yield
