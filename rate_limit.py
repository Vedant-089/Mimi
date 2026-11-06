import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import discord


RATE_LIMIT_LOG_FILE = Path("rate_limit.txt")
IST = ZoneInfo("Asia/Kolkata")


class _RateLimitResponseContext:
    def __init__(
        self,
        context: Any,
        on_response: Callable[[Any], Awaitable[None]],
    ) -> None:
        self._context = context
        self._on_response = on_response

    async def __aenter__(self) -> Any:
        response = await self._context.__aenter__()
        await self._on_response(response)
        return response

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        return await self._context.__aexit__(exc_type, exc_value, traceback)


class RateLimitLogger:
    def __init__(self) -> None:
        self._file_lock = asyncio.Lock()

    async def handle_response(self, response: Any) -> None:
        if response.status != 429:
            return

        try:
            timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            async with self._file_lock:
                with RATE_LIMIT_LOG_FILE.open("a", encoding="utf-8") as log_file:
                    log_file.write(f"Rate limited at {timestamp}\n")

            print(f"Discord rate limit recorded at {timestamp}")
        except OSError as error:
            print(f"Rate-limit logging failed: {error}")


def install_rate_limit_logging(bot: discord.Client) -> None:
    http_session = getattr(bot.http, "_HTTPClient__session", None)
    if http_session is None:
        raise RuntimeError("discord.py HTTP session is unavailable")
    if getattr(http_session, "_mimi_rate_limit_logging_installed", False):
        return

    logger = RateLimitLogger()
    original_request = http_session.request

    def request(*args: Any, **kwargs: Any) -> _RateLimitResponseContext:
        context = original_request(*args, **kwargs)
        return _RateLimitResponseContext(context, logger.handle_response)

    http_session.request = request
    http_session._mimi_rate_limit_logging_installed = True
