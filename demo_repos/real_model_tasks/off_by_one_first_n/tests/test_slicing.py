from src.slicing import first_n


def test_first_n_returns_requested_count() -> None:
    assert first_n(["a", "b", "c", "d"], 3) == ["a", "b", "c"]
