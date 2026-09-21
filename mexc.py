"""Слой метаданных фьючерсов MEXC. Часть 2 технического задания.

Фьючерсный API MEXC — отдельный продукт от спотового: свой базовый URL
(https://contract.mexc.com) и своя схема подписи. Используются только
публичные query-эндпоинты, ключи API не нужны.

Имена полей взяты из объекта контракта MEXC (contract/detail и
websocket push.contract возвращают одну структуру). Чтение полей сделано
через _pick() со списком синонимов — если MEXC снова перестроит справочник,
достаточно дописать имя в список, а не переписывать логику.

Чтобы посмотреть сырой ответ (шаг 2 из п. 2.6 ТЗ):
    python mexc.py CAKE_USDT
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
CACHE_TTL = 24 * 3600          # метаданные меняются редко, суток достаточно
TICKER_TTL = 10                # цена — наоборот, живёт секунды
HTTP_TIMEOUT = 15

_ticker_cache: dict[str, tuple[float, dict]] = {}
_memory_cache: dict[str, Any] = {"ts": 0.0, "contracts": None}


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


def fetch_raw_contract(symbol: str) -> dict:
    """Сырой объект контракта без нормализации — для сверки имён полей."""
    data = _get(DETAIL_URL, {"symbol": symbol.upper()})
    if isinstance(data, list):
        data = data[0] if data else {}
    return data or {}


def _pick(obj: dict, *names, default=None):
    """Первое присутствующее поле из списка синонимов."""
    for name in names:
        if name in obj and obj[name] is not None:
            return obj[name]
    return default


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Нормализация контракта
# --------------------------------------------------------------------------- #
def normalize(raw: dict) -> dict:
    """Приводит объект контракта MEXC к плоской структуре для калькулятора."""
    symbol = _pick(raw, "symbol", default="")
    base = _pick(raw, "baseCoin", "baseCoinName", default=symbol.split("_")[0])
    quote = _pick(raw, "quoteCoin", "quoteCoinName", default="USDT")
    price_unit = _num(_pick(raw, "priceUnit", "tickSize", "priceTick"), 0.0)
    price_scale = int(_num(_pick(raw, "priceScale", "pricePrecision"), 4))

    return {
        "symbol": symbol,
        "base": base,
        "quote": quote,
        "display_name": _pick(raw, "displayNameEn", "displayName", default=symbol),
        # Ограничения
        "max_leverage": _num(_pick(raw, "maxLeverage"), 0.0),
        "min_leverage": _num(_pick(raw, "minLeverage"), 1.0),
        "min_vol": _num(_pick(raw, "minVol"), 1.0),
        "max_vol": _num(_pick(raw, "maxVol"), 0.0),
        "contract_size": _num(_pick(raw, "contractSize", "faceValue"), 1.0) or 1.0,
        # Цена
        "price_unit": price_unit,
        "price_scale": price_scale,
        "amount_scale": int(_num(_pick(raw, "amountScale", "volScale"), 4)),
        # Комиссии
        "taker_fee": _num(_pick(raw, "takerFeeRate"), 0.0),
        "maker_fee": _num(_pick(raw, "makerFeeRate"), 0.0),
        # Тиры риска (п. 2.5 ТЗ)
        "mmr_base": _num(_pick(raw, "maintenanceMarginRate"), 0.0),
        "imr_base": _num(_pick(raw, "initialMarginRate"), 0.0),
        "risk_base_vol": _num(_pick(raw, "riskBaseVol"), 0.0),
        "risk_incr_vol": _num(_pick(raw, "riskIncrVol"), 0.0),
        "risk_incr_mmr": _num(_pick(raw, "riskIncrMmr"), 0.0),
        "risk_incr_imr": _num(_pick(raw, "riskIncrImr"), 0.0),
        "risk_level_limit": int(_num(_pick(raw, "riskLevelLimit"), 1)),
        # Флаги для списка
        "is_new": bool(_pick(raw, "isNew", default=False)),
        "is_hot": bool(_pick(raw, "isHot", default=False)),
        "is_hidden": bool(_pick(raw, "isHidden", default=False)),
    }


# --------------------------------------------------------------------------- #
# Кэш метаданных
# --------------------------------------------------------------------------- #
def _read_disk_cache() -> Optional[dict]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_disk_cache(payload: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass  # кэш — оптимизация, а не обязательство


def load_contracts(force: bool = False) -> tuple[list[dict], float, bool]:
    """Возвращает (список контрактов, время загрузки, признак свежести).

    При недоступности биржи отдаёт просроченный кэш, если он есть.
    """
    now = time.time()

    if not force and _memory_cache["contracts"] and now - _memory_cache["ts"] < CACHE_TTL:
        return _memory_cache["contracts"], _memory_cache["ts"], True

    disk = _read_disk_cache()
    if not force and disk and now - disk.get("ts", 0) < CACHE_TTL:
        _memory_cache.update(ts=disk["ts"], contracts=disk["contracts"])
        return disk["contracts"], disk["ts"], True

    try:
        raw = _get(DETAIL_URL)
        contracts = [normalize(c) for c in raw if isinstance(c, dict)]
        contracts = [c for c in contracts if c["symbol"] and not c["is_hidden"]]
        contracts.sort(key=lambda c: c["symbol"])
        _memory_cache.update(ts=now, contracts=contracts)
        _write_disk_cache({"ts": now, "contracts": contracts})
        return contracts, now, True
    except MexcError:
        if disk and disk.get("contracts"):
            _memory_cache.update(ts=disk["ts"], contracts=disk["contracts"])
            return disk["contracts"], disk["ts"], False
        raise


def get_contract(symbol: str) -> Optional[dict]:
    contracts, _, _ = load_contracts()
    symbol = symbol.upper()
    return next((c for c in contracts if c["symbol"].upper() == symbol), None)


def search(query: str, limit: int = 60) -> list[dict]:
    """Поиск по символу и названию, как в окне выбора инструмента."""
    contracts, _, _ = load_contracts()
    query = (query or "").strip().upper().replace("/", "_")
    if not query:
        # Без запроса показываем «горячие» контракты первыми.
        ranked = sorted(contracts, key=lambda c: (not c["is_hot"], c["symbol"]))
        return ranked[:limit]

    starts, contains = [], []
    for c in contracts:
        sym, base = c["symbol"].upper(), c["base"].upper()
        if sym.startswith(query) or base.startswith(query):
            starts.append(c)
        elif query in sym or query in c["display_name"].upper():
            contains.append(c)
    return (starts + contains)[:limit]


# --------------------------------------------------------------------------- #
# Цена и фандинг
# --------------------------------------------------------------------------- #
def get_ticker(symbol: str) -> dict:
    """Последняя цена контракта. Кэшируется на несколько секунд."""
    symbol = symbol.upper()
    hit = _ticker_cache.get(symbol)
    now = time.time()
    if hit and now - hit[0] < TICKER_TTL:
        return hit[1]

    data = _get(TICKER_URL, {"symbol": symbol})
    if isinstance(data, list):
        data = data[0] if data else {}
    result = {
        "symbol": symbol,
        "last": _num(_pick(data, "lastPrice", "last"), 0.0),
        "fair": _num(_pick(data, "fairPrice", "indexPrice"), 0.0),
        "change_24h": _num(_pick(data, "riseFallRate"), 0.0) * 100,
        "volume_24h": _num(_pick(data, "amount24", "volume24"), 0.0),
    }
    _ticker_cache[symbol] = (now, result)
    return result


def get_funding_rate(symbol: str) -> dict:
    """Ставка фандинга. Списывается из маржи каждые 8 часов."""
    data = _get(f"{FUNDING_URL}/{symbol.upper()}")
    return {
        "rate": _num(_pick(data, "fundingRate"), 0.0),
        "next_settle_time": _pick(data, "nextSettleTime"),
        "collect_cycle": _num(_pick(data, "collectCycle"), 8.0),
    }


# --------------------------------------------------------------------------- #
# Тиры MMR -> k  (п. 2.5 ТЗ, вариант 2)
# --------------------------------------------------------------------------- #
def tier_for_volume(contract: dict, coins: float) -> int:
    """Номер тира риска для позиции размером coins монет.

    MEXC описывает тиры не таблицей, а арифметической прогрессией:
    тир 1 держится до riskBaseVol контрактов, дальше каждые riskIncrVol
    контрактов поднимают тир на единицу, всего не больше riskLevelLimit.
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


def mmr_for_volume(contract: dict, coins: float) -> float:
    """Maintenance margin rate для позиции размером coins монет."""
    base_mmr = contract.get("mmr_base") or 0.0
    incr_mmr = contract.get("risk_incr_mmr") or 0.0
    if incr_mmr <= 0:
        return base_mmr
    return base_mmr + incr_mmr * (tier_for_volume(contract, coins) - 1)


def make_mmr_fn(contract: dict):
    """Замыкание для build_grid(mmr_fn=...) — k пересчитывается по шагам."""
    return lambda coins: mmr_for_volume(contract, coins)


def calibrate_k(entry_price: float, real_liq: float, leverage: float) -> float:
    """Вариант 1 из п. 2.5: k по фактической ликвидации из интерфейса биржи.

    k = L * (1 - liq_реальная / avg[1]), где avg[1] = цена входа шага 1.
    """
    if entry_price <= 0 or leverage <= 0:
        raise ValueError("Цена входа и плечо должны быть больше нуля.")
    return leverage * (1 - real_liq / entry_price)


def round_to_tick(price: float, contract: Optional[dict]) -> float:
    """Округление цены до шага, который принимает биржа."""
    if not contract:
        return price
    unit = contract.get("price_unit") or 0.0
    if unit <= 0:
        return price
    return round(round(price / unit) * unit, contract.get("price_scale", 8))


# --------------------------------------------------------------------------- #
# Отладка: сырой JSON одного контракта (шаг 2 из п. 2.6 ТЗ)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "CAKE_USDT"
    raw = fetch_raw_contract(target)
    if not raw:
        print(f"Контракт {target} не найден.")
        raise SystemExit(1)

    print("=== СЫРОЙ JSON ===")
    print(json.dumps(raw, indent=2, ensure_ascii=False))
    print("\n=== ПОСЛЕ НОРМАЛИЗАЦИИ ===")
    print(json.dumps(normalize(raw), indent=2, ensure_ascii=False))
