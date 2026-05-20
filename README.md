# NebulaStream

An AI-driven edge traffic control system. Three regional Cloudflare Workers probe latency, a Python control plane detects breaches and asks an LLM how to redistribute traffic, and a Milvus-backed memory biases the LLM toward fixes that recovered quickly in the past.

```
poll → detect breach → recall similar past fix → LLM proposes new weights
   ↓
apply shift → monitor recovery → score outcome → store back in memory
```

## Quick start

```bash
# 1. Configure
cp .env.example .env

# 2. Boot the full stack
make up

# 3. Open the dashboard
open http://localhost:8000        # WebSocket-driven live view
open http://localhost:3000        # Grafana (admin/admin)
open http://localhost:9090        # Prometheus
```

To run just the control plane on the host (faster dev loop):

```bash
cd apps/control-plane
pip install -r requirements.txt
make dev
# dashboard at http://localhost:8000
```

The dashboard is served by FastAPI on the same port — no separate web server needed.

## What's where

- **`apps/control-plane/`** — Python (FastAPI + asyncio). The brain.
- **`apps/dashboard/`** — Single-page HTML/JS UI. WebSocket to `/ws`.
- **`apps/edge-workers/{us,eu,apac}/`** — Cloudflare Workers, deployed to `*.workers.dev`.
- **`apps/wasm-worker/`** — Spin/WebAssembly component (TypeScript → WASM).
- **`deploy/`** — Compose, Helm, kustomize, Pulumi, ArgoCD, monitoring configs.
- **`tests/load/`** — k6 load tests.
- **`scripts/`** — small helpers: `poll_workers.py`, `verify_setup.sh`.
- **`vendor/spin`** — pinned Spin CLI binary (gitignored).

See [`DEVOPS.md`](./DEVOPS.md) for the operational layer and [`SECURITY.md`](./SECURITY.md) for the supply-chain controls.

## Verify your environment

```bash
make verify     # checks docker, node, python, wrangler, spin, milvus
```

## Architecture

```
                ┌────────────────────────────────────────────────┐
                │           Browser dashboard (8000)             │
                └──────────────────────┬─────────────────────────┘
                                       │  WebSocket /ws
                       ┌───────────────▼────────────────┐
                       │   Control plane (FastAPI)      │  ── /metrics → Prometheus
                       │   poll • detect • LLM • shift  │  ── logs (json) → Loki/…
                       └─┬─────┬──────────┬─────────────┘
                /health  │     │ Milvus   │ Ollama
                         │     │ memory   │ LLM
              ┌──────────▼─┐ ┌─▼────────┐ │
              │ edge: us   │ │ apac/eu  │ │
              │ (workers)  │ │ (workers)│ │
              └────────────┘ └──────────┘ │
                                  ┌───────▼────────┐
                                  │  llama3.2      │
                                  └────────────────┘
```

## License

Apache 2.0. See `LICENSE`.
