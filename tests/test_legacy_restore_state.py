"""The last state of the old switch "automatic import limit" is read from the
restore store of Home Assistant before and from 2026.11, and a store that
changed again only loses that state instead of failing the setup."""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.slems import _last_stored_state

STORED = SimpleNamespace(state=SimpleNamespace(state="on"))
ENTITY_ID = "switch.slems_automatic_import_limit"


def read_with(store: object) -> object:
    with patch("custom_components.slems.async_get_restore_state", return_value=store):
        return _last_stored_state(None, ENTITY_ID)


def test_store_before_2026_11() -> None:
    assert read_with(SimpleNamespace(last_states={ENTITY_ID: STORED})) is STORED
    assert read_with(SimpleNamespace(last_states={})) is None


def test_store_from_2026_11() -> None:
    store = SimpleNamespace(async_get_stored_state={ENTITY_ID: STORED}.get)
    assert read_with(store) is STORED


def test_unknown_store_loses_only_the_state() -> None:
    assert read_with(SimpleNamespace(last_states_by_entity_id={ENTITY_ID: STORED})) is None
