"""Outbound send queue: per-thread priority ordering + soft rate limits
(Plan §3.2).

Every message the bot posts into a forum thread — proxied player lines,
NPC replies, narrator lines — goes through here instead of calling
`discord.py` directly. Two things this buys:

1. Player proxies are never starved by a burst of NPC chatter or narration
   in the same thread: they always sort first.
2. A soft per-thread and per-forum cap keeps us well under Discord's actual
   rate limit, so `discord.py`'s built-in 429 handling is a safety net we
   essentially never hit, not our sole throttling strategy.

This module has zero `discord.py` imports: `send` is an injected coroutine
so unit tests can verify ordering and throttling without a gateway
connection or webhook.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum

from panem_shared.logging import get_logger

logger = get_logger(component="outbound")

THREAD_SOFT_CAP = 4
THREAD_SOFT_CAP_WINDOW_S = 2.0
FORUM_SOFT_CAP = 8
FORUM_SOFT_CAP_WINDOW_S = 2.0


class SendPriority(IntEnum):
    PLAYER = 0
    NPC = 1
    NARRATOR = 2


@dataclass(order=True)
class _QueueItem:
    sort_key: tuple[int, int]
    message: OutboundMessage = field(compare=False)


@dataclass(slots=True)
class OutboundMessage:
    thread_id: int
    forum_channel_id: int
    priority: SendPriority
    send: Callable[[], Awaitable[None]]


class TokenBucket:
    """Simple async token bucket: `capacity` tokens refill linearly over
    `window_s` seconds. `acquire()` waits until a token is available."""

    def __init__(
        self, capacity: int, window_s: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._capacity = capacity
        self._window_s = window_s
        self._clock = clock
        self._tokens = float(capacity)
        self._last_refill = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        self._last_refill = now
        refill_rate = self._capacity / self._window_s
        self._tokens = min(self._capacity, self._tokens + elapsed * refill_rate)

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                deficit = 1 - self._tokens
                wait_s = deficit / (self._capacity / self._window_s)
            await asyncio.sleep(wait_s)


class OutboundQueue:
    """One priority queue + worker task per thread; token buckets per
    thread and per forum channel shared across that forum's threads."""

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.PriorityQueue[_QueueItem]] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._thread_buckets: dict[int, TokenBucket] = {}
        self._forum_buckets: dict[int, TokenBucket] = {}
        self._seq = itertools.count()

    def _thread_bucket(self, thread_id: int) -> TokenBucket:
        bucket = self._thread_buckets.get(thread_id)
        if bucket is None:
            bucket = TokenBucket(THREAD_SOFT_CAP, THREAD_SOFT_CAP_WINDOW_S)
            self._thread_buckets[thread_id] = bucket
        return bucket

    def _forum_bucket(self, forum_channel_id: int) -> TokenBucket:
        bucket = self._forum_buckets.get(forum_channel_id)
        if bucket is None:
            bucket = TokenBucket(FORUM_SOFT_CAP, FORUM_SOFT_CAP_WINDOW_S)
            self._forum_buckets[forum_channel_id] = bucket
        return bucket

    async def enqueue(self, message: OutboundMessage) -> None:
        queue = self._queues.get(message.thread_id)
        if queue is None:
            queue = asyncio.PriorityQueue()
            self._queues[message.thread_id] = queue
        item = _QueueItem(sort_key=(int(message.priority), next(self._seq)), message=message)
        await queue.put(item)
        self._ensure_worker(message.thread_id)

    def _ensure_worker(self, thread_id: int) -> None:
        task = self._workers.get(thread_id)
        if task is None or task.done():
            self._workers[thread_id] = asyncio.create_task(self._worker(thread_id))

    async def _worker(self, thread_id: int) -> None:
        queue = self._queues[thread_id]
        thread_bucket = self._thread_bucket(thread_id)
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            message = item.message
            await thread_bucket.acquire()
            await self._forum_bucket(message.forum_channel_id).acquire()
            try:
                await message.send()
            except Exception:
                logger.exception(
                    "outbound_send_failed",
                    thread_id=message.thread_id,
                    priority=message.priority.name,
                )
            finally:
                queue.task_done()

    async def drain(self) -> None:
        """Wait for every currently-running worker to finish. Test helper."""
        workers = list(self._workers.values())
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
