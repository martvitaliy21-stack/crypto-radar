#!/usr/bin/env python3
"""Крипто-сканер: собирает перспективные монеты с CoinGecko и DexScreener
и сохраняет результат в docs/data.json для дашборда.

Запуск: python3 scanner.py
Без API-ключей — только бесплатные публичные эндпоинты.
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).parent / "docs" / "data.json"

CG = "https://api.coingecko.com/api/v3"
DS = "https://api.dexscreener.com"

HEADERS = {"User-Agent": "crypto-scanner/1.0 (personal dashboard)"}


def get_json(url, retries=3):
    """GET с повторами при 429/сетевых ошибках."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 20 * (attempt + 1)
                print(f"  429 rate limit, жду {wait}с...")
                time.sleep(wait)
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise
    return None


def fetch_markets(pages=3):
    """Топ монет по капитализации (250 за страницу)."""
    coins = []
    for page in range(1, pages + 1):
        url = (f"{CG}/coins/markets?vs_currency=usd&order=market_cap_desc"
               f"&per_page=250&page={page}&sparkline=true"
               f"&price_change_percentage=1h%2C24h%2C7d")
        print(f"CoinGecko markets, страница {page}...")
        data = get_json(url)
        if data:
            coins.extend(data)
        time.sleep(3)  # публичный лимит CoinGecko ~10-30 запросов/мин
    return coins


def fetch_trending():
    print("CoinGecko trending...")
    data = get_json(f"{CG}/search/trending")
    return data.get("coins", []) if data else []


def fetch_dex_boosted():
    """Топ токенов с платным продвижением на DexScreener — сигнал хайпа."""
    print("DexScreener boosted tokens...")
    boosts = get_json(f"{DS}/token-boosts/top/v1") or []
    # группируем адреса по сетям, детали батчами до 30 адресов
    by_chain = {}
    for b in boosts[:45]:
        by_chain.setdefault(b["chainId"], []).append(b)
    pairs = []
    for chain, items in by_chain.items():
        addrs = ",".join(i["tokenAddress"] for i in items[:30])
        data = get_json(f"{DS}/tokens/v1/{chain}/{addrs}")
        if data:
            boost_map = {i["tokenAddress"].lower(): i for i in items}
            for p in data:
                addr = (p.get("baseToken", {}).get("address") or "").lower()
                p["_boost"] = boost_map.get(addr, {})
                pairs.append(p)
        time.sleep(1)
    return pairs


def pct(x):
    return round(x, 2) if isinstance(x, (int, float)) else None


def score_coin(c, trending_ids):
    """Скор потенциала 0-100 + причины, почему монета интересна."""
    score = 0.0
    reasons = []

    mcap = c.get("market_cap") or 0
    vol = c.get("total_volume") or 0
    ch1h = c.get("price_change_percentage_1h_in_currency") or 0
    ch24 = c.get("price_change_percentage_24h_in_currency") or 0
    ch7d = c.get("price_change_percentage_7d_in_currency") or 0

    # объём относительно капитализации — главный признак интереса рынка
    if mcap > 0:
        ratio = vol / mcap
        score += min(ratio, 0.6) / 0.6 * 30
        if ratio > 0.25:
            reasons.append(f"Объём торгов {round(ratio * 100)}% от капитализации")

    # моментум
    if ch24 > 0:
        score += min(ch24, 30) / 30 * 25
        if ch24 > 8:
            reasons.append(f"+{round(ch24, 1)}% за 24ч")
    if ch7d > 0:
        score += min(ch7d, 60) / 60 * 15
        if ch7d > 20:
            reasons.append(f"+{round(ch7d, 1)}% за неделю")
    if ch1h > 2:
        score += min(ch1h, 10) / 10 * 10
        reasons.append(f"Разгоняется прямо сейчас: +{round(ch1h, 1)}% за час")

    # в трендах CoinGecko
    if c["id"] in trending_ids:
        score += 20
        reasons.append(f"В трендах CoinGecko (#{trending_ids[c['id']] + 1})")

    return round(min(score, 100), 1), reasons


def slim(c, score, reasons):
    return {
        "id": c["id"],
        "symbol": (c.get("symbol") or "").upper(),
        "name": c.get("name"),
        "image": c.get("image"),
        "price": c.get("current_price"),
        "rank": c.get("market_cap_rank"),
        "mcap": c.get("market_cap"),
        "volume": c.get("total_volume"),
        "ch1h": pct(c.get("price_change_percentage_1h_in_currency")),
        "ch24h": pct(c.get("price_change_percentage_24h_in_currency")),
        "ch7d": pct(c.get("price_change_percentage_7d_in_currency")),
        "spark": (c.get("sparkline_in_7d") or {}).get("price", [])[::4],
        "score": score,
        "reasons": reasons,
        "url": f"https://www.coingecko.com/en/coins/{c['id']}",
    }


def build():
    markets = fetch_markets()
    trending_raw = fetch_trending()
    trending_ids = {t["item"]["id"]: i for i, t in enumerate(trending_raw)}
    dex_pairs = fetch_dex_boosted()

    scored = []
    for c in markets:
        if not c.get("market_cap") or not c.get("total_volume"):
            continue
        s, r = score_coin(c, trending_ids)
        scored.append((c, s, r))

    # 🔥 Трендовые: что в трендах CoinGecko, с рыночными данными
    trending = sorted(
        (slim(c, s, r) for c, s, r in scored if c["id"] in trending_ids),
        key=lambda x: trending_ids.get(x["id"], 99),
    )

    # 🚀 Движения: сильный рост за 24ч при реальном объёме (не фейк-памп)
    movers = [slim(c, s, r) for c, s, r in sorted(scored, key=lambda x: -(x[0].get("price_change_percentage_24h_in_currency") or 0))
              if (c.get("total_volume") or 0) > 10_000_000][:12]

    # 💎 Гемы: капитализация $5–150 млн, объём > 10% капы, по скору
    gems = [slim(c, s, r) for c, s, r in sorted(scored, key=lambda x: -x[1])
            if 5_000_000 <= c["market_cap"] <= 150_000_000
            and c["total_volume"] / c["market_cap"] > 0.10][:12]

    # ⭐ Топ по скору среди всех
    top = [slim(c, s, r) for c, s, r in sorted(scored, key=lambda x: -x[1])][:12]

    # 🆕 DEX-хайп: продвигаемые токены с приличной ликвидностью
    dex = []
    seen = set()
    for p in dex_pairs:
        base = p.get("baseToken", {})
        key = (p.get("chainId"), base.get("address"))
        liq = (p.get("liquidity") or {}).get("usd") or 0
        vol24 = (p.get("volume") or {}).get("h24") or 0
        if key in seen or liq < 50_000:
            continue
        seen.add(key)
        reasons = [f"Продвигается на DexScreener (буст ×{p['_boost'].get('totalAmount', '?')})"]
        ch24 = (p.get("priceChange") or {}).get("h24")
        if isinstance(ch24, (int, float)) and ch24 > 10:
            reasons.append(f"+{round(ch24, 1)}% за 24ч")
        if liq > 0 and vol24 / liq > 1:
            reasons.append(f"Объём {round(vol24 / liq, 1)}× к ликвидности")
        dex.append({
            "symbol": base.get("symbol"),
            "name": base.get("name"),
            "chain": p.get("chainId"),
            "price": float(p.get("priceUsd") or 0) or None,
            "ch24h": pct(ch24),
            "volume": vol24,
            "liquidity": liq,
            "mcap": p.get("marketCap"),
            "reasons": reasons,
            "url": p.get("url"),
        })
    dex = dex[:12]

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "trending": trending,
        "movers": movers,
        "gems": gems,
        "top": top,
        "dex": dex,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\nГотово: {OUT}")
    print(f"  трендовые: {len(trending)}, движения: {len(movers)}, "
          f"гемы: {len(gems)}, топ: {len(top)}, DEX: {len(dex)}")


if __name__ == "__main__":
    build()
