import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

WORKERS = {
    "us-east": "http://nebula-us.nebulastream.workers.dev",
    "eu-west": "http://nebula-eu.nebulastream.workers.dev",
    "ap-south": "http://nebula-apac.nebulastream.workers.dev",
}

POLL_INTERVAL_SEC = 5
BREACH_THRESHOLD_MS = 200


@dataclass
class RegionState:
    name: str
    readings: deque = field(default_factory=lambda: deque(maxlen=10))
    last_latency_ms: float = -1.0
    in_breach: bool = False

    def record(self, latency_ms: float):
        self.readings.append(latency_ms)
        self.last_latency_ms = latency_ms

    def p95(self) -> float:
        if not self.readings:
            return 0.0
        s = sorted(self.readings)
        return s[min(int(len(s) * 0.95), len(s) - 1)]

    def status(self) -> str:
        if self.last_latency_ms < 0:
            return "🔘 warming"
        p = self.p95()
        if p < 100:
            return "🟢 healthy"
        if p < BREACH_THRESHOLD_MS:
            return "🟡 degraded"
        return "🔴 BREACH"


@dataclass
class BreachEvent:
    region: str
    p95_ms: float
    current_ms: float
    weights: dict
    timestamp: float = field(default_factory=time.time)


class LatencyPoller:
    def __init__(
        self, on_breach: Callable[[BreachEvent], Awaitable[None]] | None = None
    ):
        self.states = {r: RegionState(name=r) for r in WORKERS}
        self.on_breach = on_breach
        self.current_weights = {"us-east": 33, "eu-west": 33, "ap-south": 34}
        self.running = False

    async def _poll_once(self, client: httpx.AsyncClient) -> dict:
        async def fetch(region, url):
            t0 = time.monotonic()
            try:
                r = await client.get(f"{url}/health", timeout=5.0)
                rtt = (time.monotonic() - t0) * 1000
                r.json()  # validate JSON
                return region, rtt
            except Exception:
                return region, None

        results = await asyncio.gather(*[fetch(r, u) for r, u in WORKERS.items()])
        return dict(results)

    async def _evaluate(self, results: dict):
        breaches = []
        for region, latency in results.items():
            state = self.states[region]
            if latency is None:
                print(f"  ⚠️  {region}: no response")
                continue
            state.record(latency)
            was_breaching = state.in_breach
            state.in_breach = state.p95() >= BREACH_THRESHOLD_MS
            if state.in_breach and not was_breaching:
                print(f"\n  🚨 BREACH: {region} p95={state.p95():.1f}ms")
                breaches.append(
                    BreachEvent(
                        region=region,
                        p95_ms=state.p95(),
                        current_ms=latency,
                        weights=dict(self.current_weights),
                    )
                )
        return breaches

    def _print_table(self):
        ts = time.strftime("%H:%M:%S")
        print(f"\n{'─' * 62}")
        print(f"  NebulaStream Control Plane  [{ts}]")
        print(f"{'─' * 62}")
        print(f"  {'Region':<12} {'RTT':>8} {'p95':>8}  {'Weight':>7}  State")
        print(f"{'─' * 62}")
        for region, state in self.states.items():
            w = self.current_weights.get(region, 0)
            cur = (
                f"{state.last_latency_ms:.0f}ms" if state.last_latency_ms >= 0 else "—"
            )
            p95 = f"{state.p95():.0f}ms" if state.readings else "—"
            print(
                f"  {region:<12} {cur:>8} {p95:>8}  {str(w) + '%':>7}  {state.status()}"
            )
        print(f"{'─' * 62}")

    async def run(self):
        self.running = True
        print("NebulaStream Control Plane — measuring RTT  |  Ctrl+C to stop\n")
        async with httpx.AsyncClient() as client:
            while self.running:
                results = await self._poll_once(client)
                breaches = await self._evaluate(results)
                self._print_table()
                if breaches and self.on_breach:
                    for event in breaches:
                        await self.on_breach(event)
                await asyncio.sleep(POLL_INTERVAL_SEC)


async def _test_breach_handler(event: BreachEvent):
    print(f"\n  ⚡ breach handler: {event.region} p95={event.p95_ms:.1f}ms")
    print("     (LLM call will go here in Phase 3B)")


async def main():
    poller = LatencyPoller(on_breach=_test_breach_handler)
    await poller.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nControl plane stopped.")
