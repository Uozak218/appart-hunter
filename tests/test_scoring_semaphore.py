"""Concurrency cap on the scoring pipeline."""

from __future__ import annotations

import asyncio

from src.scoring.pipeline import _SCORING_CONCURRENCY, _get_scoring_semaphore


def test_semaphore_capacity_matches_constant():
    sem = _get_scoring_semaphore()
    assert sem._value + sum(1 for _ in sem._waiters or []) <= _SCORING_CONCURRENCY


async def test_semaphore_serialises_beyond_capacity():
    """N+1 tasks trying to acquire the semaphore should not all run at once."""
    sem = _get_scoring_semaphore()
    # reset: acquire everything, release, so we know we start at full capacity
    acquired_all = []
    for _ in range(_SCORING_CONCURRENCY):
        acquired_all.append(await asyncio.wait_for(sem.acquire(), timeout=0.1))
    for _ in acquired_all:
        sem.release()

    inflight = 0
    max_inflight = 0
    lock = asyncio.Lock()

    async def task():
        nonlocal inflight, max_inflight
        async with sem:
            async with lock:
                inflight += 1
                max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0.02)
            async with lock:
                inflight -= 1

    await asyncio.gather(*[task() for _ in range(_SCORING_CONCURRENCY + 3)])
    assert max_inflight <= _SCORING_CONCURRENCY
