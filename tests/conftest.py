"""Shared fixtures."""

from unittest.mock import patch

import pytest

from custom_components.slems import config_flow


@pytest.fixture(autouse=True)
def _no_entity_registry():
    """Flows built without Home Assistant hide no SLEMS entities (there is no registry);
    test_config_flow tests the hiding itself."""
    with (
        patch.object(config_flow, "_slems_entities", lambda hass, entry: []),
        patch.object(config_flow._SubentryHidesSlemsEntities, "_hidden_entities", lambda self: ([], [])),
    ):
        yield
