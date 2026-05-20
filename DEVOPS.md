# NebulaStream — DevOps Overview

How the project builds, deploys, observes itself, and recovers from failure.

## TL;DR

```bash
make up                # boot the full stack locally (compose + observability)
make dev               # or run just the control plane on the host
make helm-install      # deploy to a kubernetes cluster
make infra-up          # provision Cloudflare workers via Pulumi
make chaos             # run a one-shot chaos cycle
```

## Repository layout

```
NebulaStream/
├─ apps/                       # application code (the product)
│  ├─ control-plane/           # Python FastAPI brain (poll → detect → LLM → shift)
│  ├─ dashboard/               # static HTML UI, served by FastAPI
│  ├─ edge-workers/{us,eu,apac}/   # Cloudflare Workers — regional probes
│  └─ wasm-worker/             # Spin/WASM component
├─ deploy/                     # everything that ships the product
│  ├─ compose/docker-compose.yml
│  ├─ k8s/                     # raw kustomize manifests
│  ├─ helm/nebulastream/       # production Helm chart
│  ├─ infra/                   # Pulumi IaC (Cloudflare + Fermyon)
│  ├─ gitops/argocd/           # ArgoCD Applications (app-of-apps)
│  └─ monitoring/              # Prometheus + Alertmanager + Grafana config
├─ tests/load/                 # k6 scripts
├─ scripts/                    # one-off helpers (poll_workers, verify_setup)
├─ vendor/                     # vendored binaries (spin CLI + signature)
└─ .github/workflows/          # CI/CD
```

## Pipelines

| Workflow                | Trigger                          | What it does                                          |
|-------------------------|----------------------------------|-------------------------------------------------------|
| `control-plane-ci.yml`  | push/PR to `apps/control-plane/**` | ruff + mypy + pytest, build & push image, Trivy, cosign |
| `worker-ci.yml`         | push/PR to `apps/edge-workers/**`  | Validate JS, matrix-deploy 3 Cloudflare Workers       |
| `wasm-ci.yml`           | push/PR to `apps/wasm-worker/**`   | `spin build`, SBOM, artifact upload, `spin deploy`    |
| `iac.yml`               | push/PR to `deploy/infra/**`       | `pulumi preview` on PR, `pulumi up` on main           |
| `helm-lint.yml`         | charts/k8s changes               | `helm lint`, `kubeconform`, `kustomize build`         |
| `security.yml`          | push/PR/weekly                   | gitleaks, Trivy FS, CodeQL (Py+JS), SBOM              |
| `chaos.yml`             | weekly + manual                  | Inject failure, record recovery time, commit report   |
| `load.yml`              | PR                               | k6 smoke; fail on p95 > 400ms                         |
| `release.yml`           | git tag `v*`                     | Tagged image, cosign sign, GitHub Release             |

## Observability

- **Metrics:** `/metrics` on the control plane (Prometheus). Region p95, weights, breach counts, LLM latency, recovery histogram.
- **Logs:** `structlog` → JSON to stdout → picked up by your platform (Loki, CloudWatch, etc.).
- **Dashboards:** Grafana auto-provisioned with `nebulastream.json`.
- **Alerts:** 5 Prometheus rules → Alertmanager → webhook.

## Container hardening

The control plane image:
- Multi-stage build, smaller final layer
- Runs as UID 10001, non-root
- Read-only root FS (writable `/tmp` only)
- All Linux capabilities dropped
- `HEALTHCHECK` baked in

## Supply chain

Trivy + Syft + Cosign + Gitleaks + CodeQL + Dependabot. See `SECURITY.md`.

## Deployment paths

| Target           | Command                                                  |
|------------------|----------------------------------------------------------|
| Local laptop     | `make up` (or `make dev` for control plane only)         |
| Kubernetes       | `make helm-install`                                      |
| GitOps           | `kubectl apply -f deploy/gitops/argocd/app-of-apps.yaml` |
| Edge regions     | `make infra-up` (Pulumi) or `worker-ci.yml`              |
