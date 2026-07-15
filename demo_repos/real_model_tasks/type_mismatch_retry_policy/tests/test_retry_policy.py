from src.retry_policy import RetryPolicy


def test_retry_policy_accepts_integer_attempts() -> None:
    assert RetryPolicy().should_retry(1) is True
