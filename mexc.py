"""Small read-only client for public MEXC USDT perpetual market data."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

BASE_URL = "https://contract.mexc.com"
DETAIL_URL = f"{BASE_URL}/api/v1/contract/detail"
TICKER_URL = f"{BASE_URL}/api/v1/contract/ticker"
HTTP_TIMEOUT = 15
CACHE_TTL = 3600
TICKER_TTL = 2
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "contracts.json")

_contracts: dict[str, Any] = {"ts": 0.0, "items": None}
_tickers: dict[str, Any] = {"ts": 0.0, "map": None}


class MexcError(RuntimeError):
    """MEXC is unavailable or returned an invalid response."""


def normalize_symbol(value: str) -> str:
    text = (value or "").strip().upper().replace("/", "_").replace("-", "_")
    if not text:
        return ""
    if "_" in text:
        return text
    return (text[:-4] + "_USDT") if text.endswith("USDT") else text + "_USDT"


def _get(url: str) -> Any:
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MexcError(f"Не удалось получить данные MEXC: {exc}") from exc
    if isinstance(payload, dict):
        if payload.get("success") is False:
            raise MexcError(f"Ошибка MEXC: {payload.get('message') or payload.get('code')}")
        return payload.get("data", payload)
    return payload


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file)
    except OSError:
        pass


def load_contracts(force: bool = False):
    now = time.time()
    if not force and _contracts["items"] and now - _contracts["ts"] < CACHE_TTL:
        return _contracts["items"]
    disk = _read_cache()
    if not force and disk and now - disk.get("ts", 0) < CACHE_TTL:
        _contracts.update(ts=disk["ts"], items=disk["contracts"])
        return disk["contracts"]
    try:
        raw = _get(DETAIL_URL)
        items = []
        for contract in raw or []:
            symbol = str(contract.get("symbol") or "")
            base = contract.get("baseCoin") or symbol.split("_")[0]
            quote = contract.get("quoteCoin") or ""
            if not symbol or quote != "USDT" or contract.get("isHidden"):
                continue
            items.append({
                "symbol": symbol,
                "base": base,
                "max_leverage": _number(contract.get("maxLeverage")),
                "price_scale": int(_number(contract.get("priceScale"), 4)),
            })
        items.sort(key=lambda item: item["symbol"])
        _contracts.update(ts=now, items=items)
        _write_cache({"ts": now, "contracts": items})
        return items
    except MexcError:
        if disk and disk.get("contracts"):
            _contracts.update(ts=disk["ts"], items=disk["contracts"])
            return disk["contracts"]
        raise


def ticker_snapshot():
    now = time.time()
    if _tickers["map"] and now - _tickers["ts"] < TICKER_TTL:
        return {"map": _tickers["map"], "ts": _tickers["ts"], "fresh": True}
    try:
        raw = _get(TICKER_URL)
        if isinstance(raw, dict):
            raw = [raw]
        result = {}
        for item in raw or []:
            symbol = item.get("symbol")
            if not symbol:
                continue
            result[symbol] = {
                "last": _number(item.get("lastPrice", item.get("last")), 0.0),
                "change": (_number(item.get("riseFallRate"), 0.0) or 0.0) * 100,
                "volume": _number(item.get("amount24", item.get("turnover24")), 0.0) or 0.0,
            }
        _tickers.update(ts=now, map=result)
        return {"map": result, "ts": now, "fresh": True}
    except MexcError:
        if _tickers["map"]:
            return {"map": _tickers["map"], "ts": _tickers["ts"], "fresh": False}
        raise


def all_tickers():
    return ticker_snapshot()["map"]


def search(query: str, limit: int = 80):
    contracts = load_contracts()
    try:
        tickers = all_tickers()
    except MexcError:
        tickers = {}
    volume = lambda contract: -((tickers.get(contract["symbol"]) or {}).get("volume") or 0.0)
    query = normalize_symbol(query).replace("_USDT", "") if query else ""
    if not query:
        return sorted(contracts, key=volume)[:limit]
    starts = sorted((item for item in contracts if item["base"].startswith(query)), key=volume)
    contains = sorted((item for item in contracts if not item["base"].startswith(query)
                       and query in item["symbol"]), key=volume)
    return (starts + contains)[:limit]
