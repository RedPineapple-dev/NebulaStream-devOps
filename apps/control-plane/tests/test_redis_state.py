"""Unit tests for RedisState with fakeredis."""

import pytest
import fakeredis.aioredis
from redis_state import RedisState


@pytest.mark.asyncio
async def test_redis_state_fallback(monkeypatch):
    # Test fallback when redis is disabled or fails
    state = RedisState(redis_url="redis://localhost:9999", instance_id="cp-1")
    state._enabled = False
    
    assert await state.try_acquire_leader() is True
    weights = await state.load_weights({"us-east": 33, "eu-west": 33, "ap-south": 34})
    assert weights["us-east"] == 33

    await state.save_weights({"us-east": 50, "eu-west": 25, "ap-south": 25})
    loaded = await state.load_weights({"us-east": 33, "eu-west": 33, "ap-south": 34})
    assert loaded["us-east"] == 50


@pytest.mark.asyncio
async def test_redis_state_leader_election():
    fake_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    state1 = RedisState(instance_id="cp-1")
    state1._client = fake_client

    state2 = RedisState(instance_id="cp-2")
    state2._client = fake_client

    # state1 acquires leadership
    assert await state1.try_acquire_leader() is True
    assert await state1.get_leader() == "cp-1"

    # state2 fails to acquire leadership
    assert await state2.try_acquire_leader() is False

    # state1 saves weights
    await state1.save_weights({"us-east": 40, "eu-west": 30, "ap-south": 30})

    # state2 loads weights
    loaded = await state2.load_weights({})
    assert loaded["us-east"] == 40
