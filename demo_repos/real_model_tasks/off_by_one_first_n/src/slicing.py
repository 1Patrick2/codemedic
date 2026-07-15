def first_n(items: list[str], n: int) -> list[str]:
    return items[: n - 1]  # deliberate off-by-one bug
