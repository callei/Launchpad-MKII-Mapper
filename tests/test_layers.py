import pytest

from launchpad import config_store
from launchpad.layers import LayerSet


@pytest.fixture
def changes():
    return []


def _make(changes, *names, active=None):
    layers = LayerSet({n: {"pads": {}} for n in names}, active or names[0])
    layers.subscribe(lambda: changes.append(layers.active))
    return layers


def test_next_and_prev_wrap_around(changes):
    layers = _make(changes, "a", "b", "c")
    assert layers.next() == "b"
    assert layers.next() == "c"
    assert layers.next() == "a"
    assert layers.prev() == "c"
    assert changes == ["b", "c", "a", "c"]


def test_next_with_one_layer_is_not_a_change(changes):
    layers = _make(changes, "main")
    assert layers.next() == "main" and layers.prev() == "main"
    assert changes == []


def test_order_is_insertion_order_and_dict_is_shared(changes):
    raw = {"zeta": {"pads": {}}, "alpha": {"pads": {}}}
    layers = LayerSet(raw, "zeta")
    layers.add("mid")
    assert layers.names == ["zeta", "alpha", "mid"]
    assert layers.layers is raw and list(raw) == ["zeta", "alpha", "mid"]


def test_switch_to(changes):
    layers = _make(changes, "a", "b")
    assert layers.switch_to("b") is True
    assert layers.switch_to("b") is True  # already active: no change
    assert layers.switch_to("nope") is False
    assert layers.active == "b"
    assert changes == ["b"]


def test_active_pads_is_the_live_pads_dict():
    raw = {"a": {"pads": {"0,0": {"type": "color"}}}}
    layers = LayerSet(raw, "a")
    layers.active_pads["1,1"] = {"type": "color"}
    assert set(raw["a"]["pads"]) == {"0,0", "1,1"}


def test_add_starts_with_nav_pads_and_becomes_active(changes):
    layers = _make(changes, "main")
    assert layers.add("drums") is True
    assert layers.active == "drums"
    assert layers.active_pads == config_store.default_nav_pads()
    layers.active_pads["0,0"] = {"type": "color"}
    assert "0,0" not in config_store.default_nav_pads()  # a fresh copy, not shared
    assert changes == ["drums"]


def test_add_rejects_duplicate_and_empty_names(changes):
    layers = _make(changes, "main")
    assert layers.add("main") is False
    assert layers.add("") is False
    assert layers.names == ["main"] and changes == []


def test_delete_active_falls_back_to_main(changes):
    layers = _make(changes, "x", "main", "y", active="y")
    assert layers.delete("y") is True
    assert layers.names == ["x", "main"] and layers.active == "main"
    assert changes == ["main"]


def test_delete_active_without_main_falls_back_to_first(changes):
    layers = _make(changes, "a", "b", active="b")
    assert layers.delete("b") is True
    assert layers.active == "a"


def test_delete_non_active_keeps_active(changes):
    layers = _make(changes, "a", "b", "c", active="c")
    assert layers.delete("a") is True
    assert layers.names == ["b", "c"] and layers.active == "c"
    assert changes == ["c"]  # list changed: one notification


def test_delete_refuses_last_and_missing(changes):
    layers = _make(changes, "only")
    assert layers.delete("only") is False
    assert layers.delete("nope") is False
    assert layers.names == ["only"] and changes == []


def test_replace_adopts_dict_and_repairs_active(changes):
    layers = _make(changes, "main")
    raw = {"b": {"pads": {}}, "a": {"pads": {}}}
    layers.replace(raw, "gone")
    assert layers.layers is raw and layers.active == "b"
    assert changes == ["b"]


def test_replace_with_empty_layers_creates_main():
    layers = LayerSet()
    assert layers.names == ["main"] and layers.active == "main"
    raw = {}
    layers.replace(raw, None)
    assert raw == {"main": {"pads": {}}} and layers.active == "main"


def test_contains(changes):
    layers = _make(changes, "a", "b")
    assert "a" in layers and "nope" not in layers


def test_clear_keeps_only_nav_pads():
    layers = {"main": {"pads": {"0,0": {"type": "color", "color": "#ff0000"}, "2,-1": {"type": "hotkey", "keys": "a"}}}}
    ls = LayerSet(layers, "main")
    calls = []
    ls.subscribe(lambda: calls.append(1))
    assert ls.clear("main")
    assert set(ls.active_pads) == {"2,-1", "3,-1"}
    assert ls.active_pads["2,-1"] == {"type": "hotkey", "keys": "a"}  # a remapped nav pad keeps its mapping
    assert calls == [1]
    assert not ls.clear("missing")
