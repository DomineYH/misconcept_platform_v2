"""OpenAI service base class with shared configuration."""

from openai import APIConnectionError, APIError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import config

# Shared retry decorator for all OpenAI API calls
openai_retry = retry(
    retry=retry_if_exception_type(
        (APIConnectionError, APIError, RateLimitError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)


class OpenAIBaseService:
    """Base class for OpenAI-powered services.

    Provides shared client initialization and configuration.
    """

    def __init__(self):
        """Initialize OpenAI client with API key from config."""
        self.client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
