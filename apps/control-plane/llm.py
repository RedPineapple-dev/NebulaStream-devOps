import asyncio
import json
from dataclasses import dataclass

import httpx

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"
MIN_REGION_WEIGHT = 5
MAX_WEIGHT_TOTAL = 100


@dataclass
class LLMDecision:
    weights: dict
    reasoning: str
    raw_response: str
    used_fallback: bool = False


def build_prompt(latencies, weights, past_incident=None):
    latency_lines = "\n".join(f"  - {r}: {ms:.0f}ms p95" for r, ms in latencies.items())
    weight_lines = "\n".join(f"  - {r}: {w}%" for r, w in weights.items())
    memory_block = (
        f"\nSIMILAR PAST INCIDENT:\n{past_incident}\n" if past_incident else ""
    )
    return f"""You are a traffic control AI. Analyse latency and return new weights.

LATENCIES:
{latency_lines}

CURRENT WEIGHTS:
{weight_lines}
{memory_block}
RULES:
- Weights must sum to exactly 100
- No region below 5%
- Shift traffic away from high-latency regions

Respond with ONLY this JSON, no other text:
{{"weights":{{"us-east":<int>,"eu-west":<int>,"ap-south":<int>}},"reasoning":"<one sentence>"}}"""


def validate_weights(weights, current):
    regions = list(current.keys())
    for r in regions:
        if r not in weights:
            weights[r] = current[r]
        weights[r] = max(MIN_REGION_WEIGHT, int(weights[r]))
    total = sum(weights[r] for r in regions)
    if total != MAX_WEIGHT_TOTAL:
        highest = max(regions, key=lambda r: weights[r])
        weights[highest] += MAX_WEIGHT_TOTAL - total
        weights[highest] = max(MIN_REGION_WEIGHT, weights[highest])
    return {r: weights[r] for r in regions}


def rule_based_decision(latencies, current_weights):
    new_weights = dict(current_weights)
    for region, lat in latencies.items():
        if lat >= 200:
            new_weights[region] = MIN_REGION_WEIGHT
        elif lat >= 100:
            new_weights[region] = max(MIN_REGION_WEIGHT, current_weights[region] - 10)
    return LLMDecision(
        weights=validate_weights(new_weights, current_weights),
        reasoning=f"Rule-based fallback. Max latency: {max(latencies.values()):.0f}ms.",
        raw_response="[fallback]",
        used_fallback=True,
    )


async def get_llm_decision(latencies, current_weights, past_incident=None):
    prompt = build_prompt(latencies, current_weights, past_incident)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            raw = r.json()["response"].strip()
            print(f"\n  🤖 LLM response: {raw}")
            clean = raw.replace("```json", "").replace("```", "").strip()
            start = clean.find("{")
            end = clean.rfind("}") + 1
            data = json.loads(clean[start:end])
            return LLMDecision(
                weights=validate_weights(data["weights"], current_weights),
                reasoning=data.get("reasoning", ""),
                raw_response=raw,
            )
    except Exception as e:
        print(f"  ⚠️  LLM failed ({e}) — fallback")
        return rule_based_decision(latencies, current_weights)


async def test():
    latencies = {"us-east": 280.0, "eu-west": 45.0, "ap-south": 38.0}
    weights = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    print("Testing LLM decision...\n")
    d = await get_llm_decision(latencies, weights)
    print(f"\n{'─' * 50}")
    print(f"  Weights:   {d.weights}")
    print(f"  Reasoning: {d.reasoning}")
    print(f"  Fallback:  {d.used_fallback}")
    print(f"{'─' * 50}")


if __name__ == "__main__":
    asyncio.run(test())
