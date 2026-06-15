"""Publishes RawEvents onto the aegis:events Redis stream."""

from __future__ import annotations

from redis.asyncio import Redis

from stream.schema import RawEvent, to_stream_fields

EVENTS_STREAM = "aegis:events"
STREAM_MAXLEN = 10_000


class Producer:
    def __init__(self, redis: Redis, stream: str = EVENTS_STREAM, maxlen: int = STREAM_MAXLEN):
        self._redis = redis
        self._stream = stream
        self._maxlen = maxlen

    async def publish(self, event: RawEvent) -> str:
        return await self._redis.xadd(
            self._stream,
            to_stream_fields(event),
            maxlen=self._maxlen,
            approximate=True,
        )
