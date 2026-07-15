from src.math_helpers import factorial


def test_factorial_positive_integer() -> None:
    assert factorial(5) == 120
