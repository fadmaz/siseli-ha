# conftest.py — pytest/unittest discovery helper
# This file ensures the tests/ directory is recognized as a test package.
import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture(autouse=True)
def _no_test_writes_to_data(tmp_path, monkeypatch):
    """The runtime writes /data/state.json and /data/discovery_state.json. Unpatched,
    the suite wrote both on the developer's own machine -- D:\\data\\state.json on
    Windows held decoded capture values -- because every path through parse_payload
    reaches the cache writer. Each test gets a private directory instead. A test that
    patches either path itself still wins, since its patch is applied after this one."""
    from src.siseli_bridge import mqtt as mqtt_mod
    from src.siseli_bridge import parsers as parser_mod

    monkeypatch.setattr(parser_mod, "STATE_CACHE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mqtt_mod, "DISCOVERY_MARKER_FILE", str(tmp_path / "discovery_state.json"))
