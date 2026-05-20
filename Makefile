.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE := docker compose -f deploy/compose/docker-compose.yml --env-file .env

# ── Local dev ────────────────────────────────────────────────────────────────
.PHONY: up down logs ps clean
up:           ## Start full local stack (control plane + milvus + observability)
	@test -f .env || cp .env.example .env
	$(COMPOSE) --profile observability up -d --build

down:         ## Stop everything
	$(COMPOSE) --profile observability down

logs:         ## Tail control-plane logs
	$(COMPOSE) logs -f control-plane

ps:           ## List services
	$(COMPOSE) --profile observability ps

clean:        ## Stop + remove volumes (destructive)
	$(COMPOSE) --profile observability down -v
	rm -rf volumes/

# ── Run without docker (fastest dev loop) ────────────────────────────────────
.PHONY: dev
dev:          ## Run control plane on host (no docker); dashboard at http://localhost:8000
	@test -f .env || cp .env.example .env
	cd apps/control-plane && uvicorn server:app --host 127.0.0.1 --port 8000 --reload

# ── Code quality ─────────────────────────────────────────────────────────────
.PHONY: lint test fmt
lint:         ## Run all linters
	cd apps/control-plane && ruff check . && mypy --ignore-missing-imports .
	helm lint deploy/helm/nebulastream

test:         ## Run unit tests
	cd apps/control-plane && pytest -q

fmt:          ## Auto-format Python
	cd apps/control-plane && ruff format . && ruff check --fix .

# ── Build & deploy ───────────────────────────────────────────────────────────
.PHONY: docker-build k8s-apply k8s-delete helm-install helm-uninstall
docker-build: ## Build control plane image
	docker build -t ghcr.io/nebulastream/control-plane:local apps/control-plane/

k8s-apply:    ## Apply raw k8s manifests via kustomize
	kubectl apply -k deploy/k8s/

k8s-delete:
	kubectl delete -k deploy/k8s/

helm-install: ## Install via Helm
	helm dependency update deploy/helm/nebulastream
	helm upgrade --install nebulastream deploy/helm/nebulastream \
		--namespace nebulastream --create-namespace

helm-uninstall:
	helm uninstall nebulastream -n nebulastream

# ── IaC ──────────────────────────────────────────────────────────────────────
.PHONY: infra-preview infra-up infra-destroy
infra-preview:
	cd deploy/infra && pulumi preview --stack dev

infra-up:
	cd deploy/infra && pulumi up --stack dev

infra-destroy:
	cd deploy/infra && pulumi destroy --stack dev

# ── Chaos & load ─────────────────────────────────────────────────────────────
.PHONY: chaos load
chaos:        ## Run a one-shot chaos cycle
	mkdir -p reports
	cd apps/control-plane && python chaos.py --once --report ../../reports/chaos-$$(date +%Y%m%d-%H%M).json

load:         ## Run the k6 smoke test
	k6 run tests/load/smoke.js

# ── Security ─────────────────────────────────────────────────────────────────
.PHONY: scan secrets sbom
scan:
	trivy image ghcr.io/nebulastream/control-plane:local

secrets:
	gitleaks detect --source . --redact

sbom:
	syft . -o spdx-json=sbom.spdx.json

# ── Verify ───────────────────────────────────────────────────────────────────
.PHONY: verify
verify:
	./scripts/verify_setup.sh

help:         ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
