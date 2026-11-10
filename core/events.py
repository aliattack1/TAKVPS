"""Tiny pub/sub used to decouple core from UI."""
from __future__ import annotations

import inspect
from collections import defaultdict
from typing import Any, Callable

CONNECTION_STATUS = "connection.status"


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def subscribe(self, topic: str, fn: Callable[..., Any]) -> Callable[[], None]:
        self._subs[topic].append(fn)

        def unsubscribe() -> None:
            if fn in self._subs[topic]:
                self._subs[topic].remove(fn)
        return unsubscribe

    async def publish(self, topic: str, *args: Any) -> None:
        for fn in list(self._subs[topic]):
            res = fn(*args)
            if inspect.isawaitable(res):
                await res
