"""Vector memory of incidents with outcome scoring + retrieval for MVLC veto."""
from __future__ import annotations
import asyncio
import json
import logging
import httpx
from dataclasses import dataclass

log = logging.getLogger("control_plane.memory")

try:
    from config import settings
    MILVUS_HOST  = settings.milvus_host
    MILVUS_PORT  = settings.milvus_port
    OLLAMA_URL   = settings.ollama_embed_url
    OLLAMA_MODEL = settings.ollama_model
    VECTOR_DIM   = settings.vector_dim
    COLLECTION   = settings.collection
except Exception:
    MILVUS_HOST  = "localhost"
    MILVUS_PORT  = 19530
    OLLAMA_URL   = "http://localhost:11434/api/embeddings"
    OLLAMA_MODEL = "llama3.2"
    VECTOR_DIM   = 3072
    COLLECTION   = "incidents"


@dataclass
class Incident:
    region: str
    latency_ms: float
    fix_applied: str
    weights_before: dict
    weights_after: dict
    recovery_seconds: float = -1.0
    fix_worked: bool = False
    summary: str = ""


async def embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": text,
        })
        return r.json()["embedding"]


def _connect():
    from pymilvus import connections
    connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)


def get_collection():
    from pymilvus import Collection, FieldSchema, CollectionSchema, DataType, utility
    _connect()

    if utility.has_collection(COLLECTION):
        return Collection(COLLECTION)

    fields = [
        FieldSchema(name="id",                  dtype=DataType.INT64,        is_primary=True, auto_id=True),
        FieldSchema(name="embedding",           dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
        FieldSchema(name="region",              dtype=DataType.VARCHAR,      max_length=32),
        FieldSchema(name="summary",             dtype=DataType.VARCHAR,      max_length=512),
        FieldSchema(name="latency_ms",          dtype=DataType.FLOAT),
        FieldSchema(name="fix_applied",         dtype=DataType.VARCHAR,      max_length=256),
        FieldSchema(name="weights_before_json", dtype=DataType.VARCHAR,      max_length=256),
        FieldSchema(name="weights_after_json",  dtype=DataType.VARCHAR,      max_length=256),
        FieldSchema(name="recovery_seconds",    dtype=DataType.FLOAT),
        FieldSchema(name="fix_worked",          dtype=DataType.BOOL),
        FieldSchema(name="outcome_score",       dtype=DataType.FLOAT),
    ]
    schema = CollectionSchema(fields, description="NebulaStream incidents with action vectors for MVLC")
    col = Collection(COLLECTION, schema)
    col.create_index("embedding", {
        "index_type":  "FLAT",
        "metric_type": "COSINE",
        "params":      {},
    })
    return col


def calculate_outcome_score(recovery_seconds: float, fix_worked: bool) -> float:
    if not fix_worked:
        return 0.0
    if recovery_seconds <= 5:  return 1.0
    if recovery_seconds <= 15: return 0.75
    if recovery_seconds <= 30: return 0.5
    if recovery_seconds <= 60: return 0.25
    return 0.1


def _store_blocking(incident: Incident, vector: list[float]) -> bool:
    col = get_collection()
    score = calculate_outcome_score(incident.recovery_seconds, incident.fix_worked)
    col.insert([
        [vector],
        [incident.region],
        [incident.summary],
        [float(incident.latency_ms)],
        [incident.fix_applied],
        [json.dumps(incident.weights_before)],
        [json.dumps(incident.weights_after)],
        [float(incident.recovery_seconds)],
        [incident.fix_worked],
        [float(score)],
    ])
    col.flush()
    return True


async def store_incident(incident: Incident) -> bool:
    try:
        summary = (
            f"{incident.region} latency {incident.latency_ms:.0f}ms. "
            f"Fix: {incident.fix_applied}. "
            f"Recovery: {incident.recovery_seconds:.0f}s. "
            f"Outcome: {'success' if incident.fix_worked else 'failed'}."
        )
        incident.summary = summary
        vector = await embed(summary)
        return await asyncio.to_thread(_store_blocking, incident, vector)
    except Exception as e:
        log.warning("store_incident_failed: %s: %s", type(e).__name__, e)
        return False


def _search_blocking(vector: list[float], k: int) -> list[dict]:
    from pymilvus import Collection, utility
    _connect()
    if not utility.has_collection(COLLECTION):
        return []
    col = Collection(COLLECTION)
    col.load()
    results = col.search(
        data=[vector],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {}},
        limit=k,
        output_fields=[
            "region", "summary", "fix_applied",
            "weights_before_json", "weights_after_json",
            "recovery_seconds", "fix_worked", "outcome_score",
        ],
    )
    out: list[dict] = []
    if not results or not results[0]:
        return out
    for hit in results[0]:
        e = hit.entity
        try:
            wb = json.loads(e.get("weights_before_json") or "{}")
            wa = json.loads(e.get("weights_after_json")  or "{}")
        except Exception:
            wb, wa = {}, {}
        out.append({
            "region":           e.get("region"),
            "summary":          e.get("summary"),
            "fix_applied":      e.get("fix_applied"),
            "weights_before":   wb,
            "weights_after":    wa,
            "recovery_seconds": float(e.get("recovery_seconds") or -1.0),
            "fix_worked":       bool(e.get("fix_worked")),
            "outcome_score":    float(e.get("outcome_score") or 0.0),
            "similarity":       float(hit.distance),
        })
    return out


async def recall_cases(region: str, latency_ms: float, k: int = 5) -> list[dict]:
    """Return up to k past cases (with action vectors) ranked by similarity."""
    try:
        query  = f"{region} latency {latency_ms:.0f}ms degradation"
        vector = await embed(query)
        return await asyncio.to_thread(_search_blocking, vector, k)
    except Exception as e:
        log.warning("recall_cases_failed: %s: %s", type(e).__name__, e)
        return []


async def update_outcome(
    region: str,
    fix_applied: str,
    latency_ms: float,
    weights_before: dict,
    weights_after: dict,
    recovery_seconds: float,
    fix_worked: bool,
) -> bool:
    return await store_incident(Incident(
        region=region,
        latency_ms=latency_ms,
        fix_applied=fix_applied,
        weights_before=weights_before,
        weights_after=weights_after,
        recovery_seconds=recovery_seconds,
        fix_worked=fix_worked,
    ))


# Legacy single-string helper used by apps/control-plane/main.py
async def recall_similar(region: str, latency_ms: float):
    cases = await recall_cases(region, latency_ms, k=3)
    if not cases:
        return None
    best = max(cases, key=lambda c: c["outcome_score"])
    worked = "✅ worked" if best["fix_worked"] else "❌ failed"
    return (
        f"Region: {best['region']} | Fix: {best['fix_applied']} | "
        f"Recovery: {best['recovery_seconds']:.0f}s | Outcome: {worked} | "
        f"Score: {best['outcome_score']:.2f} | Similarity: {best['similarity']:.2f}"
    )
