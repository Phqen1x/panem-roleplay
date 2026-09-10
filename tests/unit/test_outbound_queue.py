from __future__ import annotations

import time

from panem_bot.outbound import (
    FORUM_SOFT_CAP,
    FORUM_SOFT_CAP_WINDOW_S,
    THREAD_SOFT_CAP,
    THREAD_SOFT_CAP_WINDOW_S,
    OutboundMessage,
    OutboundQueue,
    SendPriority,
    TokenBucket,
)


async def test_priority_ordering_within_a_thread():
    queue = OutboundQueue()
    order: list[str] = []

    async def record(label: str) -> None:
        order.append(label)

    await queue.enqueue(
        OutboundMessage(
            thread_id=1,
            forum_channel_id=100,
            priority=SendPriority.NARRATOR,
            send=lambda: record("narrator"),
        )
    )
    await queue.enqueue(
        OutboundMessage(
            thread_id=1, forum_channel_id=100, priority=SendPriority.NPC, send=lambda: record("npc")
        )
    )
    await queue.enqueue(
        OutboundMessage(
            thread_id=1,
            forum_channel_id=100,
            priority=SendPriority.PLAYER,
            send=lambda: record("player"),
        )
    )
    await queue.drain()

    assert order == ["player", "npc", "narrator"]


async def test_fifo_within_same_priority():
    queue = OutboundQueue()
    order: list[int] = []

    for i in range(5):

        async def record(i=i):
            order.append(i)

        await queue.enqueue(
            OutboundMessage(
                thread_id=1, forum_channel_id=100, priority=SendPriority.PLAYER, send=record
            )
        )
    await queue.drain()
    assert order == [0, 1, 2, 3, 4]


async def test_separate_threads_run_independently():
    queue = OutboundQueue()
    order: list[str] = []

    async def record(label: str) -> None:
        order.append(label)

    await queue.enqueue(
        OutboundMessage(
            thread_id=1,
            forum_channel_id=100,
            priority=SendPriority.PLAYER,
            send=lambda: record("t1"),
        )
    )
    await queue.enqueue(
        OutboundMessage(
            thread_id=2,
            forum_channel_id=100,
            priority=SendPriority.PLAYER,
            send=lambda: record("t2"),
        )
    )
    await queue.drain()
    assert set(order) == {"t1", "t2"}


async def test_send_exception_does_not_break_the_worker():
    queue = OutboundQueue()
    order: list[str] = []

    async def boom() -> None:
        raise RuntimeError("send failed")

    async def record() -> None:
        order.append("ok")

    await queue.enqueue(
        OutboundMessage(thread_id=1, forum_channel_id=100, priority=SendPriority.PLAYER, send=boom)
    )
    await queue.enqueue(
        OutboundMessage(
            thread_id=1, forum_channel_id=100, priority=SendPriority.PLAYER, send=record
        )
    )
    await queue.drain()
    assert order == ["ok"]


async def test_token_bucket_enforces_soft_cap():
    bucket = TokenBucket(capacity=2, window_s=0.2)
    start = time.monotonic()
    for _ in range(4):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    # 4 acquisitions against a 2-token/0.2s bucket must take >= ~0.2s
    # (the first 2 are free, the next 2 wait for a refill).
    assert elapsed >= 0.15


async def test_token_bucket_allows_burst_up_to_capacity():
    bucket = TokenBucket(capacity=3, window_s=1.0)
    start = time.monotonic()
    for _ in range(3):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


def test_soft_cap_constants_match_plan():
    # Plan §3.2: soft cap 4 msg / 2s per thread, 8 msg / 2s per forum.
    assert (THREAD_SOFT_CAP, THREAD_SOFT_CAP_WINDOW_S) == (4, 2.0)
    assert (FORUM_SOFT_CAP, FORUM_SOFT_CAP_WINDOW_S) == (8, 2.0)


async def test_fifty_messages_across_ten_scenes_all_delivered():
    """Loose analogue of T-0.1's load shape, without a live Discord guild."""
    queue = OutboundQueue()
    delivered: list[tuple[int, int]] = []

    async def record(thread_id: int, seq: int) -> None:
        delivered.append((thread_id, seq))

    for thread_id in range(10):
        for seq in range(5):
            await queue.enqueue(
                OutboundMessage(
                    thread_id=thread_id,
                    forum_channel_id=thread_id // 4,
                    priority=SendPriority.PLAYER,
                    send=lambda t=thread_id, s=seq: record(t, s),
                )
            )
    await queue.drain()
    assert len(delivered) == 50
    assert set(delivered) == {(t, s) for t in range(10) for s in range(5)}
