"""Данные с фьючерсов MEXC: список пар, цены, ставки поддерживающей маржи.

Фьючерсный API — отдельный продукт от спотового: база https://contract.mexc.com,
публичные query-эндпоинты без ключей.

Главный принцип этого модуля: отсутствие данных возвращается как None, а не как
нуль. Нуль в MMR молча превращает расчёт в идеализированный, а на плечах выше
~50x это ломает всю сетку.

Посмотреть сырой ответ по паре:  python mexc.py CAKE_USDT
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Optional

import requests

BASE_URL = "https://contract.mexc.com"
DETAIL_URL = f"{BASE_URL}/api/v1/contract/detail"
TICKER_URL = f"{BASE_URL}/api/v1/contract/ticker"
FUNDING_URL = f"{BASE_URL}/api/v1/contract/funding_rate"

CACHE_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "mexc_contracts.json")
CACHE_TTL = 24 * 3600      # метаданные контрактов меняются редко
TICKER_TTL = 10            # цены живут секунды
HTTP_TIMEOUT = 15

_contracts: dict[str, Any] = {"ts": 0.0, "list": None}
_tickers: dict[str, Any] = {"ts": 0.0, "map": None}


class MexcError(RuntimeError):
    """Биржа недоступна или вернула ошибку."""


# --------------------------------------------------------------------------- #
# Низкий уровень
# --------------------------------------------------------------------------- #
def _get(url: str, params: Optional[dict] = None) -> Any:
    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise MexcError(f"Запрос к MEXC не прошёл: {exc}") from exc
    except ValueError as exc:
        raise MexcError("MEXC вернула не-JSON ответ.") from exc

    if isinstance(payload, dict):
        if payload.get("success") is False:
            raise MexcError(f"MEXC: {payload.get('message') or payload.get('code')}")
        return payload.get("data", payload)
    return payload


def _pick(obj: dict, *names):
    """Первое присутствующее поле из списка синонимов, иначе None."""
    for name in names:
        if name in obj and obj[name] is not None:
            return obj[name]
    return None


def _num(value, default=None):
    """Число или default. По умолчанию именно None, а не нуль."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_raw_contract(symbol: str) -> dict:
    """Сырой объект контракта без нормализации — для сверки имён полей."""
    data = _get(DETAIL_URL, {"symbol": normalize_symbol(symbol)})
    if isinstance(data, list):
        data = data[0] if data else {}
    return data or {}


# --------------------------------------------------------------------------- #
# Символы
# --------------------------------------------------------------------------- #
def normalize_symbol(text: str) -> str:
    """CAKE, cake, cakeusdt, CAKE/USDT, cake_usdt -> CAKE_USDT."""
    s = (text or "").strip().upper().replace("/", "_").replace("-", "_")
    if not s:
        return ""
    if "_" in s:
        return s
    if s.endswith("USDT") and len(s) > 4:
        return s[:-4] + "_USDT"
    return s + "_USDT"


# --------------------------------------------------------------------------- #
# Нормализация контракта
# --------------------------------------------------------------------------- #
def normalize(raw: dict) -> dict:
    symbol = _pick(raw, "symbol") or ""
    base = _pick(raw, "baseCoin", "baseCoinName") or symbol.split("_")[0]
    quote = _pick(raw, "quoteCoin", "quoteCoinName") or (
        symbol.split("_")[1] if "_" in symbol else "")

    return {
        "symbol": symbol,
        "base": base,
        "quote": quote,
        "display_name": _pick(raw, "displayNameEn", "displayName") or symbol,
        # Ограничения
        "max_leverage": _num(_pick(raw, "maxLeverage")),
        "min_leverage": _num(_pick(raw, "minLeverage"), 1.0),
        "min_vol": _num(_pick(raw, "minVol"), 0.0),
        "contract_size": _num(_pick(raw, "contractSize", "faceValue"), 1.0) or 1.0,
        # Цена
        "price_unit": _num(_pick(raw, "priceUnit", "tickSize", "priceTick")),
        "price_scale": int(_num(_pick(raw, "priceScale", "pricePrecision"), 4)),
        # Комиссии — показываются справочно, в цепочку цен не входят
        "taker_fee": _num(_pick(raw, "takerFeeRate")),
        "maker_fee": _num(_pick(raw, "makerFeeRate")),
        # Тиры риска. None означает «биржа не дала», и это должно быть видно.
        "mmr_base": _num(_pick(raw, "maintenanceMarginRate")),
        "risk_base_vol": _num(_pick(raw, "riskBaseVol"), 0.0),
        "risk_incr_vol": _num(_pick(raw, "riskIncrVol"), 0.0),
        "risk_incr_mmr": _num(_pick(raw, "riskIncrMmr"), 0.0),
        "risk_level_limit": int(_num(_pick(raw, "riskLevelLimit"), 1)),
        "is_hidden": bool(_pick(raw, "isHidden")),
        "is_hot": bool(_pick(raw, "isHot")),
    }


def has_mmr(contract: Optional[dict]) -> bool:
    return bool(contract) and contract.get("mmr_base") is not None


# --------------------------------------------------------------------------- #
# Кэш метаданных
# --------------------------------------------------------------------------- #
def _read_disk() -> Optional[dict]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_disk(payload: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass


def load_contracts(force: bool = False) -> tuple[list[dict], float, bool]:
    """(список контрактов, время загрузки, признак свежести).

    Если биржа молчит — отдаёт просроченный кэш, помечая его как несвежий.
    """
    now = time.time()

    if not force and _contracts["list"] and now - _contracts["ts"] < CACHE_TTL:
        return _contracts["list"], _contracts["ts"], True

    disk = _read_disk()
    if not force and disk and now - disk.get("ts", 0) < CACHE_TTL:
        _contracts.update(ts=disk["ts"], list=disk["contracts"])
        return disk["contracts"], disk["ts"], True

    try:
        raw = _get(DETAIL_URL)
        items = [normalize(c) for c in raw if isinstance(c, dict)]
        items = [c for c in items if c["symbol"] and not c["is_hidden"]]
        items.sort(key=lambda c: c["symbol"])
        _contracts.update(ts=now, list=items)
        _write_disk({"ts": now, "contracts": items})
        return items, now, True
    except MexcError:
        if disk and disk.get("contracts"):
            _contracts.update(ts=disk["ts"], list=disk["contracts"])
            return disk["contracts"], disk["ts"], False
        raise


def usdt_perpetuals() -> list[dict]:
    """Только бессрочные фьючерсы с расчётом в USDT (USDT-M)."""
    items, _, _ = load_contracts()
    return [c for c in items if c["quote"] == "USDT" and c["symbol"].endswith("_USDT")]


def get_contract(symbol: str) -> Optional[dict]:
    """Контракт по символу в любом написании.

    Если пары нет в суточном кэше — один раз обновляет кэш: она могла
    появиться на бирже уже после последней загрузки.
    """
    target = normalize_symbol(symbol)
    if not target:
        return None

    for force in (False, True):
        items, _, _ = load_contracts(force=force)
        hit = next((c for c in items if c["symbol"] == target), None)
        if hit:
            return hit
    return None


def search(query: str, limit: int = 80) -> list[dict]:
    """Поиск по списку USDT-фьючерсов: сначала совпадения с начала."""
    items = usdt_perpetuals()
    q = normalize_symbol(query).replace("_USDT", "") if query else ""

    if not q:
        return sorted(items, key=lambda c: (not c["is_hot"], c["symbol"]))[:limit]

    starts = [c for c in items if c["base"].startswith(q)]
    inside = [c for c in items if c not in starts and q in c["symbol"]]
    return (starts + inside)[:limit]


# --------------------------------------------------------------------------- #
# Цены
# --------------------------------------------------------------------------- #
def all_tickers() -> dict[str, dict]:
    """Цены по всем контрактам одним запросом. Кэш на несколько секунд."""
    now = time.time()
    if _tickers["map"] and now - _tickers["ts"] < TICKER_TTL:
        return _tickers["map"]

    data = _get(TICKER_URL)
    if isinstance(data, dict):
        data = [data]
    result = {}
    for item in data or []:
        sym = _pick(item, "symbol")
        if not sym:
            continue
        result[sym] = {
            "last": _num(_pick(item, "lastPrice", "last"), 0.0),
            "change": (_num(_pick(item, "riseFallRate"), 0.0) or 0.0) * 100,
        }
    _tickers.update(ts=now, map=result)
    return result


def get_price(symbol: str) -> Optional[float]:
    hit = all_tickers().get(normalize_symbol(symbol))
    return hit["last"] if hit else None


def get_funding_rate(symbol: str) -> dict:
    """Ставка фандинга. В расчёт ликвидации не входит — биржа её тоже не учитывает."""
    data = _get(f"{FUNDING_URL}/{normalize_symbol(symbol)}")
    return {
        "rate": _num(_pick(data, "fundingRate"), 0.0),
        "collect_cycle": _num(_pick(data, "collectCycle"), 8.0),
    }


# --------------------------------------------------------------------------- #
# Тиры риска -> MMR
# --------------------------------------------------------------------------- #
def tier_for_volume(contract: dict, coins: float) -> int:
    """Номер тира риска для позиции размером coins монет.

    MEXC задаёт тиры прогрессией: тир 1 до riskBaseVol контрактов, дальше
    каждые riskIncrVol контрактов поднимают тир, всего не больше riskLevelLimit.
    """
    incr_vol = contract.get("risk_incr_vol") or 0.0
    base_vol = contract.get("risk_base_vol") or 0.0
    max_level = max(1, int(contract.get("risk_level_limit") or 1))
    if incr_vol <= 0:
        return 1

    vol = coins / (contract.get("contract_size") or 1.0)
    if vol <= base_vol:
        return 1
    return min(1 + math.ceil((vol - base_vol) / incr_vol), max_level)


def mmr_for_volume(contract: dict, coins: float) -> Optional[float]:
    """MMR для позиции размером coins монет, или None если биржа не дала базу."""
    base = contract.get("mmr_base")
    if base is None:
        return None
    incr = contract.get("risk_incr_mmr") or 0.0
    return base + incr * (tier_for_volume(contract, coins) - 1)


def max_mmr(contract: dict) -> Optional[float]:
    """MMR верхнего тира — худший случай для проверки допустимости плеча."""
    base = contract.get("mmr_base")
    if base is None:
        return None
    incr = contract.get("risk_incr_mmr") or 0.0
    return base + incr * (max(1, int(contract.get("risk_level_limit") or 1)) - 1)


def make_mmr_fn(contract: dict):
    """Замыкание для build_grid(mmr_fn=...): MMR пересчитывается на каждом шаге."""
    if not has_mmr(contract):
        return None
    return lambda coins: mmr_for_volume(contract, coins)


def mmr_from_real_liq(entry_price: float, real_liq: float, leverage: float) -> float:
    """Калибровка по факту: MMR = реальная_ликвидация / P1 - (1 - 1/L)."""
    if entry_price <= 0 or leverage <= 0:
        raise ValueError("Цена входа и плечо должны быть больше нуля.")
    return real_liq / entry_price - (1 - 1 / leverage)


def round_to_tick(price: float, contract: Optional[dict]) -> float:
    """Округление до шага цены, который принимает биржа."""
    if not contract:
        return price
    unit = contract.get("price_unit")
    if not unit or unit <= 0:
        return price
    return round(round(price / unit) * unit, contract.get("price_scale") or 8)


# --------------------------------------------------------------------------- #
# Отладка
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "CAKE_USDT"
    raw = fetch_raw_contract(target)
    if not raw:
        print(f"Контракт {normalize_symbol(target)} не найден.")
        raise SystemExit(1)

    print("=== СЫРОЙ JSON ===")
    print(json.dumps(raw, indent=2, ensure_ascii=False))
    print("\n=== ПОСЛЕ НОРМАЛИЗАЦИИ ===")
    norm = normalize(raw)
    print(json.dumps(norm, indent=2, ensure_ascii=False))
    print("\n=== ПРОВЕРКА ПОЛЕЙ ===")
    for key in ("mmr_base", "max_leverage", "contract_size", "price_unit",
                "price_scale", "min_vol", "taker_fee", "risk_incr_mmr"):
        value = norm[key]
        mark = "НЕТ ДАННЫХ — сверьте имя поля" if value is None else value
        print(f"  {key}: {mark}")
