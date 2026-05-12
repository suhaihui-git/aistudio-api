import asyncio

import pytest

from aistudio_api.api.state import ExclusiveSlotTimeout, RuntimeState


def test_exclusive_slot_times_out_when_request_slot_is_busy():
    async def run():
        state = RuntimeState(
            busy_lock=asyncio.Semaphore(1),
            state_lock=asyncio.Lock(),
            max_concurrency=1,
        )
        await state.busy_lock.acquire()
        try:
            with pytest.raises(ExclusiveSlotTimeout):
                async with state.exclusive_slot(timeout_seconds=0.01):
                    pass
        finally:
            state.busy_lock.release()

    asyncio.run(run())


def test_exclusive_slot_times_out_when_state_lock_is_busy():
    async def run():
        state = RuntimeState(
            busy_lock=asyncio.Semaphore(1),
            state_lock=asyncio.Lock(),
            max_concurrency=1,
        )
        async with state.state_lock:
            with pytest.raises(ExclusiveSlotTimeout):
                async with state.exclusive_slot(timeout_seconds=0.01):
                    pass

    asyncio.run(run())
