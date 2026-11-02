import asyncio
import contextvars
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import discord
import aiohttp


RATE_LIMIT_LOG_FILE = Path("rate_limit.txt")
RATE_LIMIT_CHANNEL_ID = 1566763198479343656
RATE_LIMIT_USER_ID = 760720549092917248


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
        if response.status == 429:
            await self._on_response(response)
        return response

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        return await self._context.__aexit__(exc_type, exc_value, traceback)


class RateLimitLogger:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._file_lock = asyncio.Lock()
        self._notification_lock = asyncio.Lock()
        self._sending_notification = contextvars.ContextVar(
            "mimi_sending_rate_limit_notification",
            default=False,
        )

    async def handle_response(self, response: Any) -> None:
        if self._sending_notification.get():
            return

        async with self._notification_lock:
            try:
                body_text = (await response.read()).decode("utf-8", errors="replace")
                try:
                    body: Any = json.loads(body_text)
                except json.JSONDecodeError:
                    body = body_text

                description = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": response.status,
                    "method": response.request_info.method,
                    "url": str(response.url),
                    "headers": dict(response.headers),
                    "body": body,
                }
                description_json = json.dumps(description, indent=2, ensure_ascii=False)
                async with self._file_lock:
                    with RATE_LIMIT_LOG_FILE.open("a", encoding="utf-8") as log_file:
                        if log_file.tell() > 0:
                            log_file.write("\n")
                        log_file.write(description_json)
                        log_file.write("\n")

                token = self._sending_notification.set(True)
                try:
                    await self._send_notification(description_json)
                finally:
                    self._sending_notification.reset(token)
            except (
                OSError,
                UnicodeError,
                TypeError,
                ValueError,
                aiohttp.ClientError,
                discord.DiscordException,
            ) as error:
                print(f"Rate-limit logging failed: {error}")

    async def _send_notification(self, description_json: str) -> None:
        channel = self.bot.get_channel(RATE_LIMIT_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(RATE_LIMIT_CHANNEL_ID)

        if not isinstance(channel, discord.abc.Messageable):
            raise TypeError(f"Rate-limit channel {RATE_LIMIT_CHANNEL_ID} is not messageable")

        prefix = (
            f"<@{RATE_LIMIT_USER_ID}> Mimi is getting rate limited by Discord.\n"
            "The response was saved in `rate_limit.txt`:\n"
            "```json\n"
        )
        suffix = "\n```"
        available_length = 2000 - len(prefix) - len(suffix)
        description_preview = description_json[:available_length].replace("```", "``\u200b")
        message = f"{prefix}{description_preview}{suffix}"
        await channel.send(message)


def install_rate_limit_logging(bot: discord.Client) -> None:
    http_session = getattr(bot.http, "_HTTPClient__session", None)
    if http_session is None:
        raise RuntimeError("discord.py HTTP session is unavailable")
    if getattr(http_session, "_mimi_rate_limit_logging_installed", False):
        return

    logger = RateLimitLogger(bot)
    original_request = http_session.request

    def request(*args: Any, **kwargs: Any) -> _RateLimitResponseContext:
        context = original_request(*args, **kwargs)
        return _RateLimitResponseContext(context, logger.handle_response)

    http_session.request = request
    http_session._mimi_rate_limit_logging_installed = True
