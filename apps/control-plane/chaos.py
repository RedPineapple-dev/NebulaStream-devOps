import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EDGE_DIR = REPO_ROOT / "apps" / "edge-workers"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

WORKERS = {
    "us-east": "http://nebula-us.nebulastream.workers.dev",
    "eu-west": "http://nebula-eu.nebulastream.workers.dev",
    "ap-south": "http://nebula-apac.nebulastream.workers.dev",
}

CHAOS_INTERVAL_SEC = 1800  # 30 minutes in production
DEMO_INTERVAL_SEC = 60  # 1 minute in demo mode
SLA_THRESHOLD_SEC = 15  # must detect + fix within 15s

# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class ChaosAction:
    target_region: str
    failure_type: str  # "latency" | "timeout"
    delay_ms: int
    rationale: str


@dataclass
class ChaosReport:
    action: ChaosAction
    injected_at: float
    detected_at: float | None
    fixed_at: float | None
    recall_fired: bool
    sla_passed: bool
    time_to_detect: float | None
    time_to_fix: float | None

    def print(self):
        print(f"\n{'═' * 60}")
        print("  CHAOS REPORT")
        print(f"{'═' * 60}")
        print(f"  Target:         {self.action.target_region}")
        print(f"  Failure:        {self.action.failure_type} +{self.action.delay_ms}ms")
        print(f"  Rationale:      {self.action.rationale}")
        print(
            f"  Time-to-detect: {self.time_to_detect:.1f}s"
            if self.time_to_detect
            else "  Time-to-detect: —"
        )
        print(
            f"  Time-to-fix:    {self.time_to_fix:.1f}s"
            if self.time_to_fix
            else "  Time-to-fix:    —"
        )
        print(
            f"  Memory recall:  {'✅ fired' if self.recall_fired else '❌ not fired'}"
        )
        sla = "✅ PASSED" if self.sla_passed else "❌ FAILED"
        print(f"  SLA (<15s):     {sla}")
        print(f"{'═' * 60}\n")


# ── Hypothesis generator ──────────────────────────────────────────────────────

ARCHITECTURE = """
NebulaStream has three Cloudflare Worker regions:
- us-east: handles North American traffic
- eu-west: handles European traffic
- ap-south: handles Asia-Pacific traffic

Each region exposes /health and /ping endpoints.
The control plane polls all three every 5 seconds.
A breach fires when p95 latency exceeds 200ms.
"""


async def generate_hypothesis(current_latencies: dict[str, float]) -> ChaosAction:
    lat_lines = "\n".join(f"  - {r}: {ms:.0f}ms" for r, ms in current_latencies.items())
    prompt = f"""You are a chaos engineering AI for NebulaStream.

SYSTEM ARCHITECTURE:
{ARCHITECTURE}

CURRENT LATENCIES:
{lat_lines}

Identify the single most impactful failure to inject right now.
Choose a region that would cause the most interesting self-healing response.

Respond with ONLY this JSON, no other text:
{{"target_region":"<us-east|eu-west|ap-south>","failure_type":"latency","delay_ms":<int 200-500>,"rationale":"<one sentence>"}}"""

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
            return ChaosAction(
                target_region=data["target_region"],
                failure_type=data.get("failure_type", "latency"),
                delay_ms=int(data.get("delay_ms", 300)),
                rationale=data.get("rationale", ""),
            )
    except Exception as ex:
        print(f"  ⚠️  Hypothesis generation failed ({ex}) — using default")
        return ChaosAction(
            target_region="eu-west",
            failure_type="latency",
            delay_ms=350,
            rationale="Default chaos: EU latency injection",
        )


# ── Failure injection (macOS-compatible) ─────────────────────────────────────
# tc netem is Linux-only. On macOS we simulate by deploying a slow worker.
# For the demo, we inject chaos by updating the Cloudflare Worker to add
# artificial sleep — then restore it after the chaos window.

_chaos_active = False
_chaos_region = None


async def inject_failure(action: ChaosAction) -> bool:
    global _chaos_active, _chaos_region
    print(f"\n  💥 Injecting {action.delay_ms}ms latency into {action.target_region}")

    worker_map = {
        "us-east": str(EDGE_DIR / "us"),
        "eu-west": str(EDGE_DIR / "eu"),
        "ap-south": str(EDGE_DIR / "apac"),
    }
    worker_dir = worker_map[action.target_region]
    region = action.target_region
    delay = action.delay_ms

    slow_js = f"""export default {{
  async fetch(request) {{
    await new Promise(r => setTimeout(r, {delay}));
    const start = Date.now();
    const url = new URL(request.url);
    const region = "{region}";
    const latency_ms = Date.now() - start + {delay};
    if (url.pathname === "/health") return Response.json({{ region, latency_ms, status: "degraded" }});
    if (url.pathname === "/ping")   return Response.json({{ region, latency_ms, pong: true }});
    return new Response("Not found", {{ status: 404 }});
  }}
}};"""

    try:
        with open(f"{worker_dir}/index.js", "w") as f:
            f.write(slow_js)
        result = subprocess.run(
            ["wrangler", "deploy"],
            cwd=worker_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            _chaos_active = True
            _chaos_region = action.target_region
            print(
                f"  ✅ Chaos injected — {action.target_region} now returns +{delay}ms"
            )
            return True
        else:
            print(f"  ❌ Deploy failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  ❌ Injection failed: {e}")
        return False


async def remove_failure(action: ChaosAction) -> bool:
    global _chaos_active, _chaos_region
    print(f"\n  🔧 Removing chaos from {action.target_region}")

    worker_map = {
        "us-east": (str(EDGE_DIR / "us"), "us-east"),
        "eu-west": (str(EDGE_DIR / "eu"), "eu-west"),
        "ap-south": (str(EDGE_DIR / "apac"), "ap-south"),
    }
    worker_dir, region = worker_map[action.target_region]

    normal_js = f"""export default {{
  async fetch(request) {{
    const start = Date.now();
    const url = new URL(request.url);
    const region = "{region}";
    const latency_ms = Date.now() - start;
    if (url.pathname === "/health") return Response.json({{ region, latency_ms, status: "ok" }});
    if (url.pathname === "/ping")   return Response.json({{ region, latency_ms, pong: true }});
    return new Response("Not found", {{ status: 404 }});
  }}
}};"""

    try:
        with open(f"{worker_dir}/index.js", "w") as f:
            f.write(normal_js)
        result = subprocess.run(
            ["wrangler", "deploy"],
            cwd=worker_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            _chaos_active = False
            _chaos_region = None
            print(f"  ✅ Chaos removed — {action.target_region} restored to normal")
            return True
        else:
            print(f"  ❌ Restore failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  ❌ Restore failed: {e}")
        return False


# ── Observation loop ──────────────────────────────────────────────────────────


async def observe_recovery(
    action: ChaosAction,
    injected_at: float,
    observation_window: int = 60,
) -> ChaosReport:
    print(f"\n  👁️  Observing recovery (window: {observation_window}s)...")
    detected_at = None
    fixed_at = None
    recall_fired = False
    target = action.target_region

    deadline = time.monotonic() + observation_window

    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                t0 = time.monotonic()
                r = await client.get(
                    f"{list(WORKERS.values())[list(WORKERS.keys()).index(target)]}/health"
                )
                rtt = (time.monotonic() - t0) * 1000
                r.json()

                if detected_at is None and rtt > 200:
                    detected_at = time.monotonic()
                    print(
                        f"  🔍 Breach detectable at RTT={rtt:.0f}ms (t+{detected_at - injected_at:.1f}s)"
                    )

                if detected_at and fixed_at is None and rtt < 150:
                    fixed_at = time.monotonic()
                    print(
                        f"  ✅ Recovery detected at RTT={rtt:.0f}ms (t+{fixed_at - injected_at:.1f}s)"
                    )
                    break

            except Exception:
                pass
            await asyncio.sleep(2)

    ttd = (detected_at - injected_at) if detected_at else None
    ttf = (fixed_at - injected_at) if fixed_at else None
    sla = ttf is not None and ttf <= SLA_THRESHOLD_SEC

    return ChaosReport(
        action=action,
        injected_at=injected_at,
        detected_at=detected_at,
        fixed_at=fixed_at,
        recall_fired=recall_fired,
        sla_passed=sla,
        time_to_detect=ttd,
        time_to_fix=ttf,
    )


# ── Full chaos cycle ──────────────────────────────────────────────────────────


async def run_chaos_cycle(demo_mode: bool = True) -> ChaosReport | None:
    print(f"\n{'─' * 60}")
    print(f"  🔴 CHAOS CYCLE STARTING  [{time.strftime('%H:%M:%S')}]")
    print(f"{'─' * 60}")

    # Get current latencies
    latencies = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for region, url in WORKERS.items():
            try:
                t0 = time.monotonic()
                await client.get(f"{url}/health")
                latencies[region] = (time.monotonic() - t0) * 1000
            except Exception:
                latencies[region] = 0.0

    # Generate hypothesis
    print("  🤖 Generating chaos hypothesis...")
    action = await generate_hypothesis(latencies)
    print(f"  📋 Hypothesis: inject {action.delay_ms}ms into {action.target_region}")
    print(f"  💭 Rationale:  {action.rationale}")

    # Inject failure
    injected_at = time.monotonic()
    ok = await inject_failure(action)
    if not ok:
        print("  ❌ Injection failed — aborting cycle")
        return None

    # Observe (shorter window in demo mode)
    window = 45 if demo_mode else 120
    report = await observe_recovery(action, injected_at, window)

    # Remove failure
    await remove_failure(action)

    # Print report
    report.print()
    return report


# ── Scheduler ─────────────────────────────────────────────────────────────────


async def run_scheduler(demo_mode: bool = True):
    interval = DEMO_INTERVAL_SEC if demo_mode else CHAOS_INTERVAL_SEC
    print(f"{'=' * 60}")
    print("  NebulaStream Chaos Agent")
    print(f"  Mode: {'DEMO (60s interval)' if demo_mode else 'PRODUCTION (30min)'}")
    print("  Ctrl+C to stop")
    print(f"{'=' * 60}")
    reports = []
    while True:
        report = await run_chaos_cycle(demo_mode=demo_mode)
        if report:
            reports.append(report)
        print(f"\n  Next cycle in {interval}s... (Ctrl+C to stop)\n")
        await asyncio.sleep(interval)


# ── Entry point ───────────────────────────────────────────────────────────────


async def run_once_and_report(demo_mode: bool = True, report_path: str | None = None):
    report = await run_chaos_cycle(demo_mode=demo_mode)
    if report and report_path:
        import json
        import os

        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        action = report.action
        with open(report_path, "w") as f:
            json.dump(
                {
                    "target_region": action.target_region,
                    "failure_type": action.failure_type,
                    "delay_ms": action.delay_ms,
                    "rationale": action.rationale,
                    "time_to_detect": report.time_to_detect,
                    "time_to_fix": report.time_to_fix,
                    "sla_passed": report.sla_passed,
                    "injected_at": report.injected_at,
                },
                f,
                indent=2,
            )
        md = report_path.replace(".json", ".md")
        with open(md, "w") as f:
            f.write(f"# Chaos report — {action.target_region}\n\n")
            f.write(f"- Failure: {action.failure_type} +{action.delay_ms}ms\n")
            f.write(f"- Rationale: {action.rationale}\n")
            f.write(f"- Time-to-detect: {report.time_to_detect}\n")
            f.write(f"- Time-to-fix:    {report.time_to_fix}\n")
            f.write(
                f"- SLA (<{SLA_THRESHOLD_SEC}s): {'PASS' if report.sla_passed else 'FAIL'}\n"
            )
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once", action="store_true", help="Run a single chaos cycle and exit"
    )
    parser.add_argument(
        "--prod", action="store_true", help="Production schedule (30min interval)"
    )
    parser.add_argument(
        "--report",
        default="reports/chaos-report.json",
        help="Where to write report JSON",
    )
    args = parser.parse_args()
    demo = not args.prod
    try:
        if args.once:
            report = asyncio.run(
                run_once_and_report(demo_mode=demo, report_path=args.report)
            )
            import sys

            sys.exit(0 if (report and report.sla_passed) else 1)
        else:
            asyncio.run(run_scheduler(demo_mode=demo))
    except KeyboardInterrupt:
        print("\nChaos agent stopped.")
