class RetryPolicy:
    max_retries: int = "three"  # deliberate type mismatch

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_retries
