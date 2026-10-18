import time
import copy
import pytest
from main import TTLCache

def test_cache_hit_and_miss():
    cache = TTLCache(ttl_seconds=10)
    assert cache.get("missing_key") is None
    
    cache.set("test_key", {"data": 123})
    assert cache.get("test_key") == {"data": 123}

def test_cache_expiration():
    # Cache with 1 second TTL
    cache = TTLCache(ttl_seconds=1)
    cache.set("expire_key", "value1")
    assert cache.get("expire_key") == "value1"
    
    # Wait for expiration
    time.sleep(1.2)
    assert cache.get("expire_key") is None

def test_cache_invalidation():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    
    assert cache.get("k1") == "v1"
    cache.clear()
    assert cache.get("k1") is None
    assert cache.get("k2") is None

def test_cache_mutation_isolation():
    """Verify that mutating a dictionary returned by cache does not corrupt cache if deepcopy is used."""
    cache = TTLCache(ttl_seconds=60)
    stored_dict = {"matches": [{"id": 1, "team": "Arsenal"}]}
    
    # Store a deepcopy
    cache.set("slate", copy.deepcopy(stored_dict))
    
    # Retrieve and mutate
    retrieved = cache.get("slate")
    assert retrieved is not None
    
    # If the cache implementation returns raw references, mutating retrieved alters cache
    # Verify behavior:
    retrieved["matches"].append({"id": 2, "team": "Chelsea"})
    
    # Stored original shouldn't have changed due to deep copy isolation
    fresh = cache.get("slate")
    assert fresh is not None
    assert len(fresh["matches"]) == 1
    assert fresh["matches"][0]["team"] == "Arsenal"
    assert fresh == stored_dict
