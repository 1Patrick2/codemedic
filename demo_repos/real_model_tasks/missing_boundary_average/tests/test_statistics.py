from src.statistics import average


def test_average_empty_input_is_safe() -> None:
    assert average([]) == 0.0


def test_average_values() -> None:
    assert average([2.0, 4.0]) == 3.0
