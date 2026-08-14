import asyncio
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import metrics as m
import veto as veto_mod
from auth import require_api_key
from circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from config import settings
from envoy_pusher import push_weights_to_envoy
from ewma import EWMASmoother
from logging_config import get_logger, setup_logging
from memory import recall_cases, update_outcome
from middleware import add_rate_limiter
from redis_state import RedisState
from tracing import get_tracer, setup_tracing

setup_logging()
log = get_logger("control_plane.server")

WORKERS = settings.workers
OLLAMA_URL = settings.ollama_url
OLLAMA_MODEL = settings.ollama_model
POLL_INTERVAL_SEC = settings.poll_interval_sec
BREACH_THRESHOLD_MS = settings.breach_threshold_ms
RATE_LIMIT_SEC = settings.rate_limit_sec
MIN_WEIGHT = settings.min_weight

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKER_DIRS = {
    "us-east": REPO_ROOT / "apps" / "edge-workers" / "us",
    "eu-west": REPO_ROOT / "apps" / "edge-workers" / "eu",
    "ap-south": REPO_ROOT / "apps" / "edge-workers" / "apac",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tracing
    setup_tracing(app)
    # Redis connection (best-effort)
    await redis_state.connect()
    # Load persisted weights
    default_w = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    loaded = await redis_state.load_weights(default_w)
    state.weights.update(loaded)
    task = asyncio.create_task(control_loop())
    yield
    task.cancel()
    await redis_state.close()


app = FastAPI(title="NebulaStream Control Plane", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
add_rate_limiter(
    app,
    capacity=settings.rate_limit_burst,
    refill_rate=settings.rate_limit_refill,
)

# Module-level singletons initialised before lifespan runs.
redis_state = RedisState(
    redis_url=settings.redis_url,
    instance_id=settings.instance_id,
)
llm_circuit_breaker = CircuitBreaker(
    max_failures=settings.cb_max_failures,
    reset_seconds=settings.cb_reset_seconds,
    name="ollama",
)
tracer = get_tracer("nebula.control_plane")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    return {"status": "ready", "workers": list(WORKERS.keys())}


@app.get("/metrics")
async def metrics_endpoint():
    body, content_type = m.render_metrics()
    return Response(content=body, media_type=content_type)


# ── Dashboard (served from same origin so WS/fetch are simple) ───────────────
_DASH_CANDIDATES = [
    REPO_ROOT / "apps" / "dashboard",
    REPO_ROOT / "dashboard",
    Path("/app/dashboard"),  # bind-mounted in container
]
_DASH_DIR = next((p for p in _DASH_CANDIDATES if (p / "index.html").exists()), None)

if _DASH_DIR is not None:

    @app.get("/")
    async def root():
        return FileResponse(_DASH_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=_DASH_DIR), name="dashboard")

# ── Shared state ──────────────────────────────────────────────────────────────


class State:
    def __init__(self):
        self.readings = {r: deque(maxlen=10) for r in WORKERS}
        self.latencies = {r: 0.0 for r in WORKERS}
        self.weights = {"us-east": 33, "eu-west": 33, "ap-south": 34}
        self.last_shift = 0.0
        self.shift_count = 0
        self.ai_log = []
        self.chaos_log = []
        self.injected_ms = {r: 0.0 for r in WORKERS}
        self.clients: list[WebSocket] = []
        # Per-region EWMA smoothers for latency
        self.ewma: dict[str, EWMASmoother] = {
            r: EWMASmoother(
                alpha=settings.ewma_alpha,
                threshold_ms=BREACH_THRESHOLD_MS,
                recovery_delta_ms=settings.recovery_delta_ms,
            )
            for r in WORKERS
        }

    def p95(self, region):
        if not self.readings[region]:
            return 0.0
        s = sorted(self.readings[region])
        return s[min(int(len(s) * 0.95), len(s) - 1)]

    def status(self, region):
        p = self.p95(region)
        if p == 0:
            return "warming"
        if p < 100:
            return "healthy"
        if p < BREACH_THRESHOLD_MS:
            return "degraded"
        return "breach"


state = State()

# ── WebSocket broadcast ───────────────────────────────────────────────────────


async def broadcast(event: dict):
    event["timestamp"] = time.strftime("%H:%M:%S")
    dead = []
    for ws in state.clients:
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in state.clients:
            state.clients.remove(ws)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    state.clients.append(websocket)
    # Send current state immediately on connect
    await websocket.send_text(
        json.dumps(
            {
                "type": "init",
                "weights": state.weights,
                "latencies": state.latencies,
                "ai_log": state.ai_log[-20:],
                "chaos_log": state.chaos_log[-10:],
                "timestamp": time.strftime("%H:%M:%S"),
            }
        )
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.clients.remove(websocket)


# ── Chaos inject endpoint ─────────────────────────────────────────────────────


class ChaosInjectRequest(BaseModel):
    region: str = "eu-west"
    delay: int = 400


class ChaosClearRequest(BaseModel):
    region: str = "eu-west"


@app.post("/chaos/inject", dependencies=[Depends(require_api_key)])
async def chaos_inject(body: ChaosInjectRequest):
    region, delay = body.region, body.delay
    if region not in WORKERS:
        event = {
            "type": "chaos",
            "action": "inject",
            "region": region,
            "success": False,
            "error": f"unknown region {region}",
        }
        state.chaos_log.append(event)
        await broadcast(event)
        return event
    state.injected_ms[region] = float(delay)
    m.chaos_injections_total.labels(region=region, type="latency").inc()
    event = {
        "type": "chaos",
        "action": "inject",
        "region": region,
        "delay_ms": delay,
        "success": True,
        "mode": "in-memory",
    }
    state.chaos_log.append(event)
    await broadcast(event)
    return event


@app.post("/chaos/clear", dependencies=[Depends(require_api_key)])
async def chaos_clear(body: ChaosClearRequest):
    region = body.region
    if region not in WORKERS:
        event = {
            "type": "chaos",
            "action": "clear",
            "region": region,
            "success": False,
            "error": f"unknown region {region}",
        }
        state.chaos_log.append(event)
        await broadcast(event)
        return event
    state.injected_ms[region] = 0.0
    event = {
        "type": "chaos",
        "action": "clear",
        "region": region,
        "success": True,
        "mode": "in-memory",
    }
    state.chaos_log.append(event)
    await broadcast(event)
    return event


@app.get("/api/leader")
async def get_leader():
    """Return the current leader instance ID."""
    leader = await redis_state.get_leader()
    return {
        "leader": leader,
        "self": redis_state.instance_id,
        "is_leader": leader == redis_state.instance_id or leader is None,
        "redis_connected": redis_state.connected,
        "circuit_breaker": {
            "state": llm_circuit_breaker.state.value,
            "failures": llm_circuit_breaker.failure_count,
        },
    }


# ── Control plane loop ────────────────────────────────────────────────────────


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


def rule_based_proposal(latencies, weights):
    w2 = dict(weights)
    for reg, ms in latencies.items():
        if ms >= 200:
            w2[reg] = MIN_WEIGHT
        elif ms >= 100:
            w2[reg] = max(MIN_WEIGHT, weights[reg] - 10)
    return validate_weights(w2, weights), "Rule-based fallback."


async def llm_proposal(latencies, weights):
    """Returns (new_weights, reasoning, used_fallback)."""
    lat = "\n".join(f"  - {r}: {ms:.0f}ms p95" for r, ms in latencies.items())
    w = "\n".join(f"  - {r}: {wt}%" for r, wt in weights.items())
    prompt = f"""You are a traffic control AI for NebulaStream.
LATENCIES:\n{lat}\nCURRENT WEIGHTS:\n{w}
RULES: weights sum to 100, no region below 5%, shift away from high-latency.
Respond ONLY with JSON: {{"weights":{{"us-east":<int>,"eu-west":<int>,"ap-south":<int>}},"reasoning":"<one sentence>"}}"""
    async def _call_llm():
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
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

    try:
        with tracer.start_as_current_span("llm_proposal") as span:
            span.set_attribute("nebula.regions", str(list(latencies.keys())))
            result = await llm_circuit_breaker.call(_call_llm)
            return result
    except CircuitBreakerOpen as cb_exc:
        log.warning("llm_circuit_open", error=str(cb_exc))
        w2, why = rule_based_proposal(latencies, weights)
        return w2, f"[CB OPEN] {why}", True
    except Exception as ex:
        log.warning(
            "llm_proposal_failed", error_type=type(ex).__name__, error=str(ex)[:200]
        )
        w2, why = rule_based_proposal(latencies, weights)
        return w2, why, True


async def monitor_recovery(
    region: str,
    weights_before: dict,
    weights_after: dict,
    fix_applied: str,
    breach_latency: float,
):
    """Watch the breached region until it recovers (or timeout), then store the outcome.
    Closes over module-level `state` for p95 access."""
    timeout = settings.recovery_timeout_s
    start = time.monotonic()
    log.info("recovery_monitor_start", region=region, timeout=timeout)
    while time.monotonic() - start < timeout:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        p95 = state.p95(region)
        if p95 and p95 < BREACH_THRESHOLD_MS:
            elapsed = time.monotonic() - start
            log.info("recovery_observed", region=region, recovery_seconds=elapsed)
            m.recovery_seconds.labels(region=region, outcome="recovered").observe(
                elapsed
            )
            await update_outcome(
                region=region,
                fix_applied=fix_applied,
                latency_ms=breach_latency,
                weights_before=weights_before,
                weights_after=weights_after,
                recovery_seconds=elapsed,
                fix_worked=True,
            )
            m.incidents_stored_total.inc()
            return
    log.warning("recovery_timeout", region=region, timeout=timeout)
    m.recovery_seconds.labels(region=region, outcome="timeout").observe(timeout)
    await update_outcome(
        region=region,
        fix_applied=fix_applied,
        latency_ms=breach_latency,
        weights_before=weights_before,
        weights_after=weights_after,
        recovery_seconds=float(timeout),
        fix_worked=False,
    )
    m.incidents_stored_total.inc()


async def control_loop():
    await asyncio.sleep(2)
    async with httpx.AsyncClient() as client:
        while True:
            # ── Leader election ────────────────────────────────────────────
            is_leader = await redis_state.try_acquire_leader()
            m.leader_active.set(1 if is_leader else 0)
            if not is_leader:
                log.debug("not_leader_sleeping")
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            # Poll
            poll_results = {}
            for region, url in WORKERS.items():
                t0 = time.monotonic()
                try:
                    r = await client.get(f"{url}/health", timeout=5.0)
                    rtt = (time.monotonic() - t0) * 1000
                    r.json()
                    rtt += state.injected_ms[region]
                    poll_results[region] = rtt
                    state.readings[region].append(rtt)
                    state.latencies[region] = rtt
                except Exception:
                    poll_results[region] = None

            # Update Prometheus gauges
            for r in WORKERS:
                m.region_latency_ms.labels(region=r).set(state.latencies[r])
                m.region_p95_ms.labels(region=r).set(state.p95(r))
                m.region_weight_pct.labels(region=r).set(state.weights[r])
                m.record_status(r, state.status(r))

            # Broadcast poll
            await broadcast(
                {
                    "type": "poll",
                    "latencies": {r: round(state.latencies[r], 1) for r in WORKERS},
                    "p95": {r: round(state.p95(r), 1) for r in WORKERS},
                    "weights": state.weights,
                    "status": {r: state.status(r) for r in WORKERS},
                }
            )

            # Update EWMA smoothers and Prometheus EWMA gauge
            for r in WORKERS:
                state.ewma[r].update(state.p95(r))
                m.ewma_p95_ms.labels(region=r).set(state.ewma[r].value)

            # Check breach using EWMA (hysteresis prevents flapping)
            breaching = [r for r in WORKERS if state.ewma[r].should_trigger]
            can_shift = (time.monotonic() - state.last_shift) >= RATE_LIMIT_SEC

            if breaching and can_shift:
                for r in breaching:
                    m.breaches_total.labels(region=r).inc()
                latencies_p95 = {r: state.p95(r) for r in WORKERS}
                primary_region = breaching[0]
                breach_latency = latencies_p95[primary_region]
                weights_before = dict(state.weights)

                await broadcast(
                    {
                        "type": "ai_thinking",
                        "message": f"Breach detected in {', '.join(breaching)}. Querying LLM…",
                        "latencies": latencies_p95,
                    }
                )

                # ── 1. LLM proposes ─────────────────────────────────────────
                t_llm = time.monotonic()
                proposed, reasoning, used_fallback = await llm_proposal(
                    latencies_p95, weights_before
                )
                m.llm_latency_seconds.observe(time.monotonic() - t_llm)
                m.llm_calls_total.labels(
                    outcome="error" if used_fallback else "success"
                ).inc()

                # ── 2. MVLC veto: retrieve cases, score, decide ─────────────
                verdict_dict = None
                if settings.veto_enabled and not used_fallback:
                    cases = await recall_cases(
                        primary_region, breach_latency, k=settings.veto_k
                    )
                    verdict = veto_mod.evaluate(
                        proposed_weights=proposed,
                        current_weights=weights_before,
                        past_cases=cases,
                        threshold=settings.veto_threshold,
                        min_cases=settings.veto_min_cases,
                    )
                    m.veto_confidence.observe(verdict.confidence)
                    verdict_dict = verdict.to_dict()
                    verdict_dict["cases_available"] = len(cases)

                    if verdict.cases_considered < settings.veto_min_cases:
                        m.vetoes_total.labels(outcome="skipped").inc()
                    elif verdict.vetoed:
                        m.vetoes_total.labels(outcome="vetoed").inc()
                        safe, safe_reason = rule_based_proposal(
                            latencies_p95, weights_before
                        )
                        log.warning(
                            "mvlc_veto",
                            region=primary_region,
                            confidence=verdict.confidence,
                            threshold=verdict.threshold,
                            cases=verdict.cases_considered,
                            llm_proposal=proposed,
                            safe_proposal=safe,
                        )
                        await broadcast(
                            {
                                "type": "ai_veto",
                                "region": primary_region,
                                "verdict": verdict_dict,
                                "llm_proposal": proposed,
                                "applied_proposal": safe,
                                "reasoning": (
                                    f"MVLC veto — {verdict.reason}. "
                                    f"Applying conservative fallback instead."
                                ),
                            }
                        )
                        proposed = safe
                        reasoning = (
                            f"[VETOED by memory] LLM said: {reasoning}. " + safe_reason
                        )
                        used_fallback = True
                    else:
                        m.vetoes_total.labels(outcome="accepted").inc()
                        reasoning = (
                            f"{reasoning} "
                            f"[MVLC confidence {verdict.confidence:.2f} from "
                            f"{verdict.cases_considered} historical cases]"
                        )

                # ── 3. Apply ────────────────────────────────────────────
                m.shifts_total.labels(
                    outcome="fallback" if used_fallback else "llm"
                ).inc()
                weights_after = proposed
                state.weights = weights_after
                state.last_shift = time.monotonic()
                state.shift_count += 1
                # Persist to Redis and push to Envoy (fire-and-forget)
                asyncio.create_task(redis_state.save_weights(weights_after))
                asyncio.create_task(push_weights_to_envoy(weights_after))

                fix_desc = ", ".join(
                    f"{r} {weights_before[r]}%→{weights_after[r]}%"
                    for r in weights_before
                )

                log.info(
                    "ai_decision",
                    shift=state.shift_count,
                    breaching=breaching,
                    new_weights=weights_after,
                    reasoning=reasoning,
                    fallback=used_fallback,
                    veto=verdict_dict,
                )
                log_entry = {
                    "type": "ai_decision",
                    "shift": state.shift_count,
                    "breaching": breaching,
                    "new_weights": weights_after,
                    "reasoning": reasoning,
                    "fallback": used_fallback,
                    "veto": verdict_dict,
                }
                state.ai_log.append(log_entry)
                await broadcast(log_entry)

                # ── 4. Persist + monitor recovery for the memory loop ───────
                asyncio.create_task(
                    monitor_recovery(
                        region=primary_region,
                        weights_before=weights_before,
                        weights_after=weights_after,
                        fix_applied=fix_desc,
                        breach_latency=breach_latency,
                    )
                )

            await asyncio.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
