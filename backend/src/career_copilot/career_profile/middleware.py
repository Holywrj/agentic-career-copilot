"""Bound the singleton's streamed bodies and prevent private response caching."""

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_CAREER_PROFILE_BODY_BYTES = 128 * 1024


class CareerProfileResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") != "/career-profile":
            await self.app(scope, receive, send)
            return
        received_bytes = 0

        async def bounded_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > MAX_CAREER_PROFILE_BODY_BYTES:
                    raise HTTPException(413, "request_too_large")
            return message

        async def no_store_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, bounded_receive, no_store_send)
