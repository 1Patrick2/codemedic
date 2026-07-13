"""Tests for the sample project — some will fail due to deliberate bugs."""

import pytest

from src.utils import add, multiply, divide, factorial
from src.services import Config, DataService


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
        # BUG: factorial is broken — NameError due to variable typo
        assert factorial(5) == 120  # will fail with NameError
        assert factorial(0) == 1
        assert factorial(1) == 1

    def test_factorial_negative(self) -> None:
        with pytest.raises(ValueError):
            factorial(-1)


class TestDataService:
    def test_config_defaults(self) -> None:
        config = Config()
        assert config.debug is False
        assert config.timeout == 30
        # BUG: max_retries is 'three' (str) instead of the annotated int
        assert isinstance(config.max_retries, int)

    def test_compute_score(self) -> None:
        config = Config()
        service = DataService(config)
        result = service.compute_score([1.0, 2.0, 3.0])
        assert result == 6.0

    def test_compute_weighted(self) -> None:
        config = Config()
        service = DataService(config)
        # BUG: compute_weighted has a NameError ('weight' not defined)
        result = service.compute_weighted([1.0, 2.0], [0.5, 0.5])
        assert result == 1.5
