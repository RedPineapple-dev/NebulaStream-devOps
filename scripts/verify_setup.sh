#!/bin/bash

echo "=============================="
echo "  NebulaStream Setup Verifier"
echo "=============================="
echo ""

PASS=0
FAIL=0

check() {
  local name=$1
  local cmd=$2
  if eval "$cmd" &>/dev/null; then
    echo "✅  $name — OK"
    PASS=$((PASS+1))
  else
    echo "❌  $name — NOT FOUND or FAILED"
    FAIL=$((FAIL+1))
  fi
}

# Node.js 20+
NODE_VER=$(node -e "process.exit(parseInt(process.versions.node) >= 20 ? 0 : 1)" 2>/dev/null)
if node -e "process.exit(parseInt(process.versions.node) >= 20 ? 0 : 1)" &>/dev/null; then
  echo "✅  Node.js 20+ — $(node --version)"
  PASS=$((PASS+1))
else
  echo "❌  Node.js 20+ — Found $(node --version 2>/dev/null || echo 'NOT INSTALLED')"
  FAIL=$((FAIL+1))
fi

# Python 3.11+
if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" &>/dev/null; then
  echo "✅  Python 3.11+ — $(python3 --version)"
  PASS=$((PASS+1))
else
  echo "❌  Python 3.11+ — Found $(python3 --version 2>/dev/null || echo 'NOT INSTALLED')"
  FAIL=$((FAIL+1))
fi

check "Wrangler CLI"   "wrangler --version"
check "Spin CLI"       "spin --version"
check "Pulumi CLI"     "pulumi version"
check "Docker"         "docker info"

# Milvus container
if docker ps 2>/dev/null | grep -q "milvus-standalone"; then
  echo "✅  Milvus (Docker) — Running on port 19530"
  PASS=$((PASS+1))
else
  echo "❌  Milvus (Docker) — Container not running"
  FAIL=$((FAIL+1))
fi

# Milvus port reachable
if curl -sf http://localhost:9091/healthz &>/dev/null; then
  echo "✅  Milvus Health Check — Responding"
  PASS=$((PASS+1))
else
  echo "❌  Milvus Health Check — Not responding (give it 30s after start)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=============================="
echo "  Results: $PASS passed, $FAIL failed"
echo "=============================="

[ $FAIL -eq 0 ] && echo "🚀 All systems go — ready for Phase 2!" || echo "⚠️  Fix the above before proceeding."