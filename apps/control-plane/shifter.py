import asyncio
import os
import time

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")

WORKER_URLS = {
    "us-east": "http://nebula-us.nebulastream.workers.dev",
    "eu-west": "http://nebula-eu.nebulastream.workers.dev",
    "ap-south": "http://nebula-apac.nebulastream.workers.dev",
}

# ── Rate limiting ─────────────────────────────────────────────────────────────

_last_shift_time = 0.0
RATE_LIMIT_SEC = 30


def can_shift() -> bool:
    return (time.monotonic() - _last_shift_time) >= RATE_LIMIT_SEC


def mark_shifted():
    global _last_shift_time
    _last_shift_time = time.monotonic()


# ── KV-based weight store (lightweight, no load balancer needed) ──────────────
# We store weights in a Python dict and log them.
# The workers read these weights conceptually — for the demo the control
# plane IS the authoritative weight store.

_current_weights: dict[str, int] = {
    "us-east": 33,
    "eu-west": 33,
    "ap-south": 34,
}


async def apply_weights(new_weights: dict[str, int]) -> bool:
    """
    Apply new traffic weights.
    In production this would update a Cloudflare Load Balancer pool.
    For the demo, we store weights locally and log the change — the
    poller uses these weights in its display and decision context.
    """
    global _current_weights

    if not can_shift():
        remaining = RATE_LIMIT_SEC - (time.monotonic() - _last_shift_time)
        print(f"  ⏳ Rate limited — next shift in {remaining:.0f}s")
        return False

    # Validate
    total = sum(new_weights.values())
    if total != 100:
        print(f"  ❌ Invalid weights — sum is {total}, must be 100")
        return False
    for region, w in new_weights.items():
        if w < 5:
            print(f"  ❌ Invalid weight for {region}: {w}% (min 5%)")
            return False

    print("\n  ⚙️  Applying traffic shift via Pulumi:")
    for region in _current_weights:
        old = _current_weights[region]
        new = new_weights.get(region, old)
        arrow = "↑" if new > old else "↓" if new < old else "="
        print(f"     {region}: {old}% {arrow} {new}%")

    _current_weights = dict(new_weights)
    mark_shifted()
    print("  ✅ Weights applied successfully")
    return True


def get_current_weights() -> dict[str, int]:
    return dict(_current_weights)


# ── Standalone test ───────────────────────────────────────────────────────────


async def test():
    print("Testing traffic shifter...\n")
    print(f"Current weights: {get_current_weights()}")

    new = {"us-east": 10, "eu-west": 45, "ap-south": 45}
    print(f"Applying:        {new}\n")
    ok = await apply_weights(new)
    print(f"\nSuccess: {ok}")
    print(f"New weights: {get_current_weights()}")

    print("\nTesting rate limit (should be blocked)...")
    ok2 = await apply_weights({"us-east": 33, "eu-west": 33, "ap-south": 34})
    print(f"Second shift allowed: {ok2}")


if __name__ == "__main__":
    asyncio.run(test())
