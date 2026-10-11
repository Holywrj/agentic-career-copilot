"""Count raw multipart bytes before spooling, and prevent private response caches."""

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from career_copilot.resume.uploads import MAX_RESUME_REQUEST_BYTES


class ResumeResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == "/resumes" or path.startswith("/resumes/")
        ):
            await self.app(scope, receive, send)
            return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_RESUME_REQUEST_BYTES:
                    raise HTTPException(413, "request_too_large")
            return message

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, bounded_receive, private_send)
