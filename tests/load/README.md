# Load tests (k6)

```bash
# Smoke (30s, 2 VUs)
k6 run smoke.js

# Ramp (6 min, up to 100 VUs)
k6 run ramp.js

# Against local control plane
WORKER_US=http://localhost:8000 k6 run smoke.js
```

CI runs `smoke.js` on every PR via `.github/workflows/load.yml` and fails the build if p95 > 400ms.
