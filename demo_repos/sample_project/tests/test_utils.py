"""Tests for mathematical helpers."""

import pytest

from src.utils.math_helpers import add, multiply, divide, factorial


class TestMathHelpers:
    def test_add(self) -> None:
        assert add(2, 3) == 5
        assert add(-1, 1) == 0

    def test_multiply(self) -> None:
        assert multiply(3, 4) == 12
        assert multiply(0, 5) == 0

    def test_divide(self) -> None:
        assert divide(10, 2) == 5.0
        with pytest.raises(ZeroDivisionError):
            divide(1, 0)

    def test_factorial(self) -> None:
        assert factorial(5) == 120
        assert factorial(0) == 1
        assert factorial(1) == 1

    def test_factorial_negative(self) -> None:
        with pytest.raises(ValueError):
            factorial(-1)


class TestEdgeCases:
    def test_add_floats(self) -> None:
        assert add(0.1, 0.2) == pytest.approx(0.3)

    def test_multiply_by_zero(self) -> None:
        assert multiply(100, 0) == 0

    def test_divide_negative(self) -> None:
        assert divide(-6, 3) == -2.0
