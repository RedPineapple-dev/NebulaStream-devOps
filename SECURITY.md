# Security

## Reporting vulnerabilities
Please email security@nebulastream.local — do not open public issues.

## Supply chain controls
- **Signed container images** — `cosign sign` runs on every push to main and every tag.
  Verify: `cosign verify ghcr.io/<org>/control-plane:<tag>`
- **SBOMs** — generated via `syft` and attached to Docker builds; also produced for WASM artifacts.
- **Vulnerability scanning** — Trivy scans images + filesystem on every push; SARIF uploaded to GitHub Security tab.
- **Secret scanning** — `gitleaks` runs in CI and pre-commit.
- **CodeQL** — Python + JavaScript code scanning, weekly.
- **Dependabot** — weekly PRs across pip, npm, Docker, and GitHub Actions.

## Runtime hardening
- Containers run as non-root (UID 10001), read-only root FS, all capabilities dropped.
- Kubernetes NetworkPolicy restricts egress to Milvus, Ollama, and HTTPS only.
- Secrets are pulled from a Kubernetes Secret (never baked into the image).
