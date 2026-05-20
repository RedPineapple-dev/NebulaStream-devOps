import httpx
import asyncio
import time
from collections import defaultdict

WORKERS = {
    "us-east":  "https://nebula-us-zkszv62k.fermyon.app",
    "eu-west":  "https://nebula-eu-nhut550p.fermyon.app",
    "ap-south": "https://nebula-apac-hasjbfha.fermyon.app",
}

readings: dict[str, list[float]] = defaultdict(list)

def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * 0.95)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]

async def poll_once(client: httpx.AsyncClient) -> dict[str, dict]:
    results = {}
    tasks = {
        region: client.get(f"{url}/health", timeout=5)
        for region, url in WORKERS.items()
    }
    responses = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for region, resp in zip(tasks.keys(), responses):
        if isinstance(resp, Exception):
            results[region] = {"latency_ms": None, "status": "ERROR", "error": str(resp)}
        else:
            data = resp.json()
            readings[region].append(data["latency_ms"])
            if len(readings[region]) > 10:
                readings[region].pop(0)
            results[region] = {
                "latency_ms": data["latency_ms"],
                "p95_ms": round(p95(readings[region]), 1),
                "status": data["status"],
            }
    return results

def print_table(results: dict, ts: str):
    print(f"\n{'─'*55}")
    print(f"  NebulaStream Latency Poll  [{ts}]")
    print(f"{'─'*55}")
    print(f"  {'Region':<12} {'Current':>10} {'p95 (10x)':>12}  Status")
    print(f"{'─'*55}")
    for region, data in results.items():
        if data["latency_ms"] is None:
            print(f"  {region:<12} {'—':>10} {'—':>12}  ❌ {data['error']}")
        else:
            flag = "🟢" if data["p95_ms"] < 100 else "🟡" if data["p95_ms"] < 200 else "🔴"
            print(f"  {region:<12} {str(data['latency_ms'])+'ms':>10} {str(data['p95_ms'])+'ms':>12}  {flag} {data['status']}")
    print(f"{'─'*55}")

async def main():
    print("NebulaStream poller starting — Ctrl+C to stop\n")
    async with httpx.AsyncClient() as client:
        while True:
            ts = time.strftime("%H:%M:%S")
            results = await poll_once(client)
            print_table(results, ts)
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nPoller stopped.")
