from wattstack_ingestion.cache import Cache


def test_get_returns_none_for_missing_key(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    assert cache.get("nope") is None


def test_set_then_get_round_trips(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cache.set("key", {"a": 1, "b": [1, 2, 3]})
    assert cache.get("key") == {"a": 1, "b": [1, 2, 3]}


def test_set_overwrites_existing_key(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cache.set("key", "first")
    cache.set("key", "second")
    assert cache.get("key") == "second"


def test_clear_with_prefix_only_removes_matching_keys(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cache.set("elexon:a", 1)
    cache.set("elexon:b", 2)
    cache.set("neso:a", 3)
    removed = cache.clear(prefix="elexon:")
    assert removed == 2
    assert cache.get("neso:a") == 3
    assert cache.get("elexon:a") is None


def test_cache_persists_across_instances(tmp_path):
    path = tmp_path / "c.sqlite"
    Cache(path).set("key", "value")
    assert Cache(path).get("key") == "value"
