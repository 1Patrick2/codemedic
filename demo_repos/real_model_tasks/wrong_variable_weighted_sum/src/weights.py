def weighted_sum(values: list[int], weights: list[int]) -> int:
    return sum(value * weight for value, weight in zip(values, weight))  # deliberate bug
