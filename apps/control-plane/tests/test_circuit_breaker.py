"""Unit tests for the CircuitBreaker module."""

import asyncio

import pytest

from circuit_breaker import CBState, CircuitBreaker, CircuitBreakerOpen


@pytest.fixture
def cb():
    return CircuitBreaker(max_failures=2, reset_seconds=0.1, name="test_cb")


@pytest.mark.asyncio
async def test_circuit_breaker_closed_state(cb):
    assert cb.state == CBState.CLOSED

    async def ok_fn():
        return "success"

    res = await cb.call(ok_fn)
    assert res == "success"
    assert cb.failure_count == 0


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_failures(cb):
    async def fail_fn():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await cb.call(fail_fn)
    assert cb.state == CBState.CLOSED
    assert cb.failure_count == 1

    with pytest.raises(ValueError):
        await cb.call(fail_fn)
    assert cb.state == CBState.OPEN
    assert cb.failure_count == 2

    # Now calls should immediately fail with CircuitBreakerOpen
    with pytest.raises(CircuitBreakerOpen):
        await cb.call(fail_fn)


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_recovery(cb):
    async def fail_fn():
        raise ValueError("boom")

    async def ok_fn():
        return "recovered"

    for _ in range(2):
        with pytest.raises(ValueError):
            await cb.call(fail_fn)

    assert cb.state == CBState.OPEN

    # Wait for reset timeout
    await asyncio.sleep(0.15)

    # Next call probe succeeds
    res = await cb.call(ok_fn)
    assert res == "recovered"
    assert cb.state == CBState.CLOSED
    assert cb.failure_count == 0
