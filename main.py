import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DATA_PATH = Path(__file__).resolve().parent / "data" / "web3_market_snapshot.csv"


@dataclass
class MarketAsset:
    asset: str
    price_usd: float
    change_24h: float
    volume_change: float
    social_mentions: int
    liquidity_score: float
    holder_change: float
    narrative: str


class ProviderError(RuntimeError):
    pass


def load_assets(path=DATA_PATH):
    with path.open(newline="", encoding="utf-8") as file:
        rows = csv.DictReader(file)
        return [
            MarketAsset(
                asset=row["asset"],
                price_usd=float(row["price_usd"]),
                change_24h=float(row["change_24h"]),
                volume_change=float(row["volume_change"]),
                social_mentions=int(row["social_mentions"]),
                liquidity_score=float(row["liquidity_score"]),
                holder_change=float(row["holder_change"]),
                narrative=row["narrative"],
            )
            for row in rows
        ]


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def score_asset(asset):
    momentum = asset.change_24h * 0.85 + asset.volume_change * 0.18 + asset.holder_change * 0.95
    attention = min(asset.social_mentions / 450.0, 16.0)
    liquidity = asset.liquidity_score * 0.22
    risk_penalty = max(0.0, 55.0 - asset.liquidity_score) * 0.35
    score = clamp(34.0 + momentum + attention + liquidity - risk_penalty)
    return round(score, 2)


def market_phase(score):
    if score >= 74:
        return "breakout-watch"
    if score >= 62:
        return "accumulation"
    if score >= 48:
        return "neutral"
    return "risk-off"


def analyze_market(assets, limit=5):
    ranked = sorted(((score_asset(asset), asset) for asset in assets), reverse=True, key=lambda item: item[0])
    return [
        {
            "asset": asset.asset,
            "score": score,
            "phase": market_phase(score),
            "price_usd": asset.price_usd,
            "change_24h": asset.change_24h,
            "volume_change": asset.volume_change,
            "liquidity_score": asset.liquidity_score,
            "narrative": asset.narrative,
        }
        for score, asset in ranked[:limit]
    ]


def build_prompt(results):
    payload = json.dumps(results, indent=2)
    return (
        "You are AI-Analy Web3, an AI market intelligence layer for Web3 teams. "
        "Read the structured market signal payload and write a concise analyst memo. "
        "Focus on momentum, liquidity, narrative strength, and execution risk. "
        "Do not provide financial advice.\n\n"
        f"Market signals:\n{payload}"
    )


def post_json(url, payload, headers=None, timeout=60):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise ProviderError(f"Provider request failed: {error}") from error
    except json.JSONDecodeError as error:
        raise ProviderError("Provider returned invalid JSON.") from error


def call_mock(results):
    top = results[0]
    return (
        f"{top['asset']} is the strongest current signal with a {top['score']} "
        f"{top['phase']} score. The main driver is {top['narrative']}, supported by "
        f"{top['change_24h']}% price movement and {top['volume_change']}% volume expansion. "
        "Use this as a research brief, not financial advice."
    )


def call_ollama(prompt, model):
    response = post_json(
        "http://localhost:11434/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.35, "num_predict": 450},
        },
    )
    return response.get("message", {}).get("content", "").strip()


def call_openai_compatible(prompt, provider, model):
    if provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY")
        url = "https://openrouter.ai/api/v1/chat/completions"
        default_model = "openai/gpt-4o-mini"
    else:
        key = os.getenv("OPENAI_API_KEY")
        url = "https://api.openai.com/v1/chat/completions"
        default_model = "gpt-4o-mini"

    if not key:
        env_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "OPENAI_API_KEY"
        raise ProviderError(f"Missing {env_name}. Use --provider mock for offline mode.")

    response = post_json(
        url,
        {
            "model": model or default_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.35,
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    return response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


def generate_memo(results, provider, model):
    prompt = build_prompt(results)
    if provider == "mock":
        return call_mock(results)
    if provider == "ollama":
        return call_ollama(prompt, model or "llama3.2")
    if provider in {"openrouter", "openai"}:
        return call_openai_compatible(prompt, provider, model)
    raise ProviderError(f"Unsupported provider: {provider}")


def parse_args():
    parser = argparse.ArgumentParser(description="Run AI-Analy Web3 market intelligence demo.")
    parser.add_argument("--provider", choices=["mock", "ollama", "openrouter", "openai"], default="mock")
    parser.add_argument("--model", help="Model name for Ollama, OpenRouter, or OpenAI.")
    parser.add_argument("--limit", type=int, default=5, help="Number of ranked assets to show.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of a text report.")
    return parser.parse_args()


def main():
    args = parse_args()
    results = analyze_market(load_assets(), limit=args.limit)

    if args.json:
        print(json.dumps({"engine": "ai-analy-web3", "signals": results}, indent=2))
        return

    print("AI-Analy Web3")
    print("Web3 Market Intelligence Snapshot\n")

    for rank, item in enumerate(results, start=1):
        print(f"{rank}. {item['asset']} | score={item['score']:.2f} | phase={item['phase']}")
        print(f"   {item['narrative']} | 24h={item['change_24h']}% | volume={item['volume_change']}%")

    try:
        memo = generate_memo(results, args.provider, args.model)
    except ProviderError as error:
        print(f"\nProvider error: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"\nAI Memo ({args.provider})")
    print(memo)


if __name__ == "__main__":
    main()
