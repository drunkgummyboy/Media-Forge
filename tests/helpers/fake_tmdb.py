"""A tiny in-process stand-in for the TMDB API, for tests."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer


@dataclass
class Reply:
    status: int = 200
    json: Any = None
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict[str, str])
    disconnect: bool = False


@dataclass
class Seen:
    path: str
    query: dict[str, str]
    headers: dict[str, str]


class FakeTmdb:
    def __init__(self) -> None:
        self.replies: dict[str, list[Reply]] = defaultdict(list)
        self.seen: list[Seen] = []
        self.url = ""

    def add(self, path: str, *replies: Reply) -> None:
        """Queue replies for a path; the last one repeats once the queue runs out."""
        self.replies[path].extend(replies)

    def requests_to(self, path: str) -> list[Seen]:
        return [s for s in self.seen if s.path == path]

    async def handle(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        self.seen.append(Seen(path, dict(request.query), dict(request.headers)))
        queue = self.replies.get(path)
        if not queue:
            return web.Response(status=404)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if reply.disconnect:
            assert request.transport is not None
            request.transport.close()
            return web.Response(status=500)
        if reply.body is not None:
            return web.Response(status=reply.status, body=reply.body, headers=reply.headers)
        return web.json_response(reply.json, status=reply.status, headers=reply.headers)


@asynccontextmanager
async def running_fake() -> AsyncIterator[FakeTmdb]:
    fake = FakeTmdb()
    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", fake.handle)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    try:
        yield fake
    finally:
        await server.close()
