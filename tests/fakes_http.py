"""Scripted stand-in for ``requests.Session`` used by the Google tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeResponse:
    status_code: int = 200
    body: Any = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def content(self) -> bytes:
        return json.dumps(self.body).encode() if self.body is not None else b""

    def json(self) -> Any:
        if self.body is None:
            raise ValueError("no json")
        return self.body


Handler = Callable[[str, str, dict[str, Any]], FakeResponse]


class FakeHttp:
    """Records every call; replies via ``handler`` or a queue of canned responses."""

    def __init__(
        self, handler: Handler | None = None, queue: list[FakeResponse] | None = None
    ) -> None:
        self.handler = handler
        self.queue = list(queue or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if self.handler:
            return self.handler(method, url, kwargs)
        return self.queue.pop(0)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    def calls_to(self, method: str) -> list[tuple[str, dict[str, Any]]]:
        return [(url, kw) for m, url, kw in self.calls if m == method]
