import asyncio

from aistudio_api.application.api_service import _iter_with_stream_heartbeat


async def _delayed_events():
    await asyncio.sleep(0.03)
    yield ("body", "ok")


def test_iter_with_stream_heartbeat_yields_keepalive_before_delayed_event():
    async def run():
        events = []
        async for event in _iter_with_stream_heartbeat(_delayed_events(), heartbeat_seconds=0.01):
            events.append(event)
        return events

    result = asyncio.run(run())

    assert ("heartbeat", None) in result
    assert result[-1] == ("body", "ok")
