import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

from memory import Incident, recall_similar, store_incident, update_outcome

WORKERS = {
    "us-east": "http://nebula-us.nebulastream.workers.dev",
    "eu-west": "http://nebula-eu.nebulastream.workers.dev",
    "ap-south": "http://nebula-apac.nebulastream.workers.dev",
}

POLL_INTERVAL_SEC = 5
BREACH_THRESHOLD_MS = 200
RATE_LIMIT_SEC = 30
MIN_WEIGHT = 5
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"


@dataclass
class RegionState:
    name: str
    readings: deque = field(default_factory=lambda: deque(maxlen=10))
    last_latency_ms: float = -1.0
    in_breach: bool = False

    def record(self, ms: float):
        self.readings.append(ms)
        self.last_latency_ms = ms

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


def validate_weights(weights, current):
    regions = list(current.keys())
    for r in regions:
        if r not in weights:
            weights[r] = current[r]
        weights[r] = max(MIN_WEIGHT, int(weights[r]))
    total = sum(weights[r] for r in regions)
    if total != 100:
        highest = max(regions, key=lambda r: weights[r])
        weights[highest] += 100 - total
        weights[highest] = max(MIN_WEIGHT, weights[highest])
    return {r: weights[r] for r in regions}


def rule_based(latencies, current):
    w = dict(current)
    for r, ms in latencies.items():
        if ms >= 200:
            w[r] = MIN_WEIGHT
        elif ms >= 100:
            w[r] = max(MIN_WEIGHT, current[r] - 10)
    return validate_weights(
        w, current
    ), f"Rule-based. Max: {max(latencies.values()):.0f}ms."


def build_prompt(latencies, weights, past_incident=None):
    lat = "\n".join(f"  - {r}: {ms:.0f}ms p95" for r, ms in latencies.items())
    w = "\n".join(f"  - {r}: {wt}%" for r, wt in weights.items())
    mem = ""
    if past_incident:
        mem = f"""
SIMILAR PAST INCIDENT (ranked by outcome score — higher score = faster recovery):
{past_incident}

Prefer fixes similar to high-scoring past incidents.
"""
    return f"""You are a traffic control AI for NebulaStream.

LATENCIES:
{lat}

CURRENT WEIGHTS:
{w}
{mem}
RULES:
- Weights must sum to exactly 100
- No region below 5%
- Shift traffic away from high-latency regions
- Prefer fixes that worked well historically

Respond with ONLY this JSON, no other text:
{{"weights":{{"us-east":<int>,"eu-west":<int>,"ap-south":<int>}},"reasoning":"<one sentence>"}}"""


async def get_decision(latencies, weights, past_incident=None):
    prompt = build_prompt(latencies, weights, past_incident)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            raw = r.json()["response"].strip()
            clean = raw.replace("```json", "").replace("```", "").strip()
            s, e = clean.find("{"), clean.rfind("}") + 1
            data = json.loads(clean[s:e])
            return (
                validate_weights(data["weights"], weights),
                data.get("reasoning", ""),
                False,
            )
    except Exception as ex:
        print(f"  ⚠️  LLM failed ({ex}) — fallback")
        w, r = rule_based(latencies, weights)
        return w, r, True


# ── Recovery monitor ──────────────────────────────────────────────────────────


async def monitor_recovery(
    region: str,
    fix_applied: str,
    latency_at_breach: float,
    states: dict,
    timeout_sec: int = 120,
) -> tuple[float, bool]:
    """
    Watches the breached region after a fix is applied.
    Returns (recovery_seconds, fix_worked).
    """
    print(f"\n  👁️  Monitoring recovery for {region}...")
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        state = states[region]
        if state.p95() < BREACH_THRESHOLD_MS and state.last_latency_ms >= 0:
            elapsed = time.monotonic() - start
            print(f"  ✅ {region} recovered in {elapsed:.1f}s")
            return elapsed, True
    print(f"  ❌ {region} did not recover within {timeout_sec}s")
    return timeout_sec, False


# ── Control Plane ─────────────────────────────────────────────────────────────


class ControlPlane:
    def __init__(self):
        self.states = {r: RegionState(name=r) for r in WORKERS}
        self.weights = {"us-east": 33, "eu-west": 33, "ap-south": 34}
        self.last_shift = 0.0
        self.shift_count = 0
        self.pending_recovery: list[asyncio.Task] = []

    def can_shift(self):
        return (time.monotonic() - self.last_shift) >= RATE_LIMIT_SEC

    def cooldown(self):
        return max(0, RATE_LIMIT_SEC - (time.monotonic() - self.last_shift))

    async def poll(self, client):
        async def fetch(region, url):
            t0 = time.monotonic()
            try:
                r = await client.get(f"{url}/health", timeout=5.0)
                rtt = (time.monotonic() - t0) * 1000
                r.json()
                return region, rtt
            except Exception:
                return region, None

        return dict(await asyncio.gather(*[fetch(r, u) for r, u in WORKERS.items()]))

    async def evaluate(self, results):
        breaching = []
        for region, ms in results.items():
            state = self.states[region]
            if ms is None:
                continue
            state.record(ms)
            was = state.in_breach
            state.in_breach = state.p95() >= BREACH_THRESHOLD_MS
            if state.in_breach and not was:
                print(f"\n  🚨 BREACH: {region} p95={state.p95():.1f}ms")
                breaching.append(region)
        return breaching

    async def handle_breach(self, breaching):
        if not self.can_shift():
            print(f"  ⏳ Rate limited — {self.cooldown():.0f}s remaining")
            return

        latencies = {r: self.states[r].p95() for r in WORKERS}
        primary_region = breaching[0]
        breach_latency = latencies[primary_region]

        # ── Recall best past fix ──────────────────────────────────────────
        print(f"\n  🔍 Querying incident memory for {primary_region}...")
        past = await recall_similar(primary_region, breach_latency)
        if past:
            print("  📖 Past incident recalled — outcome score included")
        else:
            print("  📭 No similar past incident found")

        # ── LLM decision ──────────────────────────────────────────────────
        print("  🧠 Calling LLM...")
        new_weights, reasoning, used_fallback = await get_decision(
            latencies, self.weights, past
        )

        tag = "📋 rule-based" if used_fallback else "🤖 LLM"
        print(f"  {tag} reasoning: {reasoning}")

        # ── Apply weights ─────────────────────────────────────────────────
        old_weights = dict(self.weights)
        fix_desc = ", ".join(
            f"{r} {old_weights[r]}%→{new_weights[r]}%" for r in self.weights
        )

        print("\n  ⚙️  Applying traffic shift:")
        for r in self.weights:
            old, new = self.weights[r], new_weights[r]
            arrow = "↑" if new > old else "↓" if new < old else "="
            print(f"     {r}: {old}% {arrow} {new}%")

        self.weights = new_weights
        self.last_shift = time.monotonic()
        self.shift_count += 1
        print(f"  ✅ Shift #{self.shift_count} applied")

        # ── Store initial incident (recovery unknown yet) ──────────────────
        await store_incident(
            Incident(
                region=primary_region,
                latency_ms=breach_latency,
                fix_applied=fix_desc,
                weights_before=old_weights,
                weights_after=new_weights,
                recovery_seconds=-1.0,
                fix_worked=False,
            )
        )

        # ── Monitor recovery in background ────────────────────────────────
        async def track_and_update():
            recovery_sec, worked = await monitor_recovery(
                primary_region, fix_desc, breach_latency, self.states
            )
            print(
                f"\n  📊 Updating outcome: {primary_region} recovered={worked} in {recovery_sec:.1f}s"
            )
            await update_outcome(
                region=primary_region,
                fix_applied=fix_desc,
                latency_ms=breach_latency,
                weights_before=old_weights,
                weights_after=new_weights,
                recovery_seconds=recovery_sec,
                fix_worked=worked,
            )

        task = asyncio.create_task(track_and_update())
        self.pending_recovery.append(task)

    def print_table(self):
        ts = time.strftime("%H:%M:%S")
        cd = f" | cooldown {self.cooldown():.0f}s" if not self.can_shift() else ""
        print(f"\n{'─' * 65}")
        print(f"  NebulaStream  [{ts}]{cd}")
        print(f"{'─' * 65}")
        print(f"  {'Region':<12} {'RTT':>8} {'p95':>8}  {'Weight':>7}  State")
        print(f"{'─' * 65}")
        for region, state in self.states.items():
            w = self.weights.get(region, 0)
            cur = (
                f"{state.last_latency_ms:.0f}ms" if state.last_latency_ms >= 0 else "—"
            )
            p95 = f"{state.p95():.0f}ms" if state.readings else "—"
            print(
                f"  {region:<12} {cur:>8} {p95:>8}  {str(w) + '%':>7}  {state.status()}"
            )
        print(f"{'─' * 65}")

    async def run(self):
        print("=" * 65)
        print("  NebulaStream — Poll → Detect → Recall → LLM → Shift → Learn")
        print("=" * 65)
        async with httpx.AsyncClient() as client:
            while True:
                results = await self.poll(client)
                breaching = await self.evaluate(results)
                self.print_table()
                if breaching:
                    await self.handle_breach(breaching)
                await asyncio.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        asyncio.run(ControlPlane().run())
    except KeyboardInterrupt:
        print("\nControl plane stopped.")
