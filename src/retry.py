"""
Retry & Self-Healing — Wraps critical operations with retry logic.
"""

import time
import functools


def retry(max_attempts: int = 3, delay: float = 2.0, backoff: float = 2.0,
          exceptions: tuple = (Exception,), on_fail: callable = None):
    """
    Decorator that retries a function on failure.

    Args:
        max_attempts: Maximum retry attempts
        delay: Initial delay between retries (seconds)
        backoff: Multiply delay by this after each retry
        exceptions: Tuple of exception types to catch
        on_fail: Optional callback(func_name, error, attempt) on each failure
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_error = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if on_fail:
                        on_fail(func.__name__, e, attempt)
                    if attempt < max_attempts:
                        print(f"[RETRY] {func.__name__} failed (attempt {attempt}/{max_attempts}): {e}")
                        print(f"[RETRY] Retrying in {current_delay:.0f}s...")
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        print(f"[RETRY] {func.__name__} failed after {max_attempts} attempts: {e}")

            raise last_error

        return wrapper
    return decorator
