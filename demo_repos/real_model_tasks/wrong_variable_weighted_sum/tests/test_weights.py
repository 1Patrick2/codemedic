from src.weights import weighted_sum


def test_weighted_sum() -> None:
    assert weighted_sum([2, 3], [4, 5]) == 23
