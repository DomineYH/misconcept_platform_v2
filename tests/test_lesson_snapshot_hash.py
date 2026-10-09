"""Independent canonical UTF-8 JSON fixture for the persisted config hash."""

import pytest

from src.services.lesson_snapshots import canonical_hash


def test_canonical_hash_preserves_unicode_literals_and_array_order():
    value = {"z": [2, 1], "text": "한 {x}", "a": 1}
    assert (
        canonical_hash(value)
        == "794f228291a6a6a3f215a297fa464490eca92735e02f7a2f58d2036abdba1c25"
    )
    assert canonical_hash(
        {"a": 1, "text": "한 {x}", "z": [2, 1]}
    ) == canonical_hash(value)
    assert canonical_hash(
        {"a": 1, "text": "한 {x}", "z": [1, 2]}
    ) != canonical_hash(value)
    with pytest.raises(ValueError):
        canonical_hash({"a": float("nan")})
