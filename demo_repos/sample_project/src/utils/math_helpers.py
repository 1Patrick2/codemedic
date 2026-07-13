"""Mathematical helper utilities.

Contains deliberately buggy code for CodeMedic investigator to diagnose.

Bug list:
  1. factorial() uses undefined variable name ('resut' instead of 'result')
"""

from typing import Union

Number = Union[int, float]


def add(a: Number, b: Number) -> Number:
    """Return a + b."""
    return a + b


def multiply(a: Number, b: Number) -> Number:
    """Return a * b."""
    return a * b


def divide(a: Number, b: Number) -> Number:
    """Return a / b. Raises ZeroDivisionError if b is 0."""
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


def factorial(n: int) -> int:
    """Return n! for non-negative integers.

    BUG: variable name typo — `resut` should be `result`.
    """
    if n < 0:
        raise ValueError("factorial not defined for negative numbers")
    if n == 0:
        return 1
    resut = 1  # <-- BUG: typo 'resut' instead of 'result'
    for i in range(1, n + 1):
        resut *= i
    return result  # <-- BUG: NameError — 'result' is not defined (should be 'resut')
