"""Centralized config loaded from environment with sane defaults."""
from __future__ import annotations
import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    workers: dict = field(default_factory=lambda: {
        "us-east":  _env("WORKER_US_EAST_URL",  "https://nebula-us.nebulastream.workers.dev"),
        "eu-west":  _env("WORKER_EU_WEST_URL",  "https://nebula-eu.nebulastream.workers.dev"),
        "ap-south": _env("WORKER_AP_SOUTH_URL", "https://nebula-apac.nebulastream.workers.dev"),
    })

    poll_interval_sec:   int   = field(default_factory=lambda: _env_int("POLL_INTERVAL_SEC", 5))
    breach_threshold_ms: float = field(default_factory=lambda: _env_float("BREACH_THRESHOLD_MS", 200.0))
    rate_limit_sec:      int   = field(default_factory=lambda: _env_int("RATE_LIMIT_SEC", 30))
    min_weight:          int   = field(default_factory=lambda: _env_int("MIN_WEIGHT", 5))

    ollama_url:        str = field(default_factory=lambda: _env("OLLAMA_URL", "http://host.docker.internal:11434/api/generate"))
    ollama_embed_url:  str = field(default_factory=lambda: _env("OLLAMA_EMBEDDINGS_URL", "http://host.docker.internal:11434/api/embeddings"))
    ollama_model:      str = field(default_factory=lambda: _env("OLLAMA_MODEL", "llama3.2"))

    milvus_host: str = field(default_factory=lambda: _env("MILVUS_HOST", "milvus-standalone"))
    milvus_port: int = field(default_factory=lambda: _env_int("MILVUS_PORT", 19530))
    collection:  str = field(default_factory=lambda: _env("COLLECTION", "incidents"))
    vector_dim:  int = field(default_factory=lambda: _env_int("VECTOR_DIM", 3072))

    log_level:  str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    log_format: str = field(default_factory=lambda: _env("LOG_FORMAT", "json"))

    # ── Memory-Vetoed LLM Control (MVLC) ────────────────────────────────────
    veto_enabled:       bool  = field(default_factory=lambda: _env("VETO_ENABLED", "true").lower() == "true")
    veto_threshold:     float = field(default_factory=lambda: _env_float("VETO_THRESHOLD", 0.4))
    veto_k:             int   = field(default_factory=lambda: _env_int("VETO_K", 5))
    veto_min_cases:     int   = field(default_factory=lambda: _env_int("VETO_MIN_CASES", 2))
    recovery_timeout_s: int   = field(default_factory=lambda: _env_int("RECOVERY_TIMEOUT_SEC", 120))


settings = Settings()
