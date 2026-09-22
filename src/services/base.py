"""Shared Responses call policy and explicit client ownership."""

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config import config


def is_retryable(error):
    return isinstance(error, APIConnectionError) or (
        isinstance(error, APIStatusError)
        and (error.status_code in {408, 409, 429} or error.status_code >= 500)
    )


openai_retry = retry(
    retry=retry_if_exception(is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)


class OpenAIBaseService:
    """Owned clients close with the service; injected clients belong to callers."""

    def __init__(self, *, client=None):
        if client is not None and getattr(client, "max_retries", 0) != 0:
            raise ValueError("Injected OpenAI clients must use max_retries=0")
        self._owns_client = client is None
        self.client = (
            client
            if client is not None
            else AsyncOpenAI(api_key=config.OPENAI_API_KEY, max_retries=0)
        )

    @openai_retry
    async def create_response(self, **kwargs):
        # Retry only the HTTP operation, never prompt parsing or tutor counters.
        return await self.client.responses.create(**kwargs)

    async def close(self):
        if self._owns_client:
            await self.client.close()
            self._owns_client = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
