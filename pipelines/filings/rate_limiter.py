"""
Rate Limiter & Backoff Manager for NSE Filing Analysis Pipeline

Handles:
    - Token-bucket rate limiting for API calls
    - Exponential backoff for rate limit errors
    - Request throttling per model
    - Graceful degradation under load
"""

import time
import logging
from typing import Callable, Any
from functools import wraps

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket for rate limiting."""
    
    def __init__(self, capacity: int, refill_rate: float):
        """
        Args:
            capacity: Max tokens in bucket
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.time()
    
    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if successful."""
        now = time.time()
        elapsed = now - self.last_refill
        
        # Refill tokens
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_rate
        )
        self.last_refill = now
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
    
    def wait_for(self, tokens: int = 1):
        """Block until tokens are available."""
        while not self.consume(tokens):
            time.sleep(0.1)


class RateLimiter:
    """Manages rate limits across multiple API endpoints."""
    
    def __init__(self):
        # TPM = Tokens Per Minute
        # Groq compound-mini: ~100k TPM
        # Groq openai/gpt-oss-120b: 8k TPM (BOTTLENECK)
        # Groq llama-3.1-8b: ~100k TPM
        
        self.buckets = {
            "groq.summary": TokenBucket(capacity=1000, refill_rate=100/60),      # ~100 tokens/sec
            "groq.web": TokenBucket(capacity=1000, refill_rate=100/60),          # ~100 tokens/sec
            "groq.reasoning": TokenBucket(capacity=300, refill_rate=8000/60),    # ~133 tokens/sec (but careful: 8k TPM = 133/sec)
            "nse.fetch": TokenBucket(capacity=10, refill_rate=1/5),              # 1 request every 5 seconds
            "telegram": TokenBucket(capacity=30, refill_rate=1/2),               # 1 message every 2 seconds
        }
        
        self.backoff_state = {}  # Track backoff for each endpoint
        self.backoff_attempts = {}  # Track retry attempt count per endpoint
    
    def wait(self, endpoint: str, tokens: int = 1):
        """Wait until tokens available for endpoint."""
        if endpoint not in self.buckets:
            logger.warning(f"Unknown rate limit endpoint: {endpoint}")
            return
        
        # Check if in backoff state
        if endpoint in self.backoff_state:
            backoff_until = self.backoff_state[endpoint]
            sleep_time = backoff_until - time.time()
            if sleep_time > 0:
                logger.info(f"Rate limit backoff on {endpoint} for {sleep_time:.1f}s")
                time.sleep(sleep_time)
                del self.backoff_state[endpoint]
            # Reset attempts after wait is complete.
            self.backoff_attempts[endpoint] = 0
        
        self.buckets[endpoint].wait_for(tokens)
    
    def on_rate_limit_error(self, endpoint: str, retry_after_sec: int = 60):
        """Called when API returns rate limit error. Sets exponential backoff."""
        attempts = self.backoff_attempts.get(endpoint, 0)
        backoff = retry_after_sec * (2 ** attempts)
        backoff = min(backoff, 300)  # Cap at 5 minutes
        
        logger.warning(f"Rate limit hit on {endpoint}. Backing off for {backoff}s")
        self.backoff_state[endpoint] = time.time() + backoff
        self.backoff_attempts[endpoint] = attempts + 1


# Global instance
rate_limiter = RateLimiter()


def with_rate_limit(endpoint: str, tokens: int = 1):
    """Decorator to apply rate limiting to a function."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            rate_limiter.wait(endpoint, tokens)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def with_retry(max_attempts: int = 3, backoff_factor: float = 2.0):
    """Decorator to retry function with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts:
                        wait_time = backoff_factor ** (attempt - 1)
                        logger.warning(f"{func.__name__} failed (attempt {attempt}/{max_attempts}). Retrying in {wait_time}s. Error: {e}")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts. Error: {e}")
            raise last_exc
        return wrapper
    return decorator
