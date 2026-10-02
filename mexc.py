"""MEXC Futures market data, private account, and order API client."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
import requests

BASE_URL = "https://api.mexc.com"
HTTP_TIMEOUT = 15
CONTRACT_TTL = 10
TICKER_TTL = 2
ORDER_PAGE_SIZE = 100
ORDER_PAGE_LIMIT = 100

_contracts: dict[str, Any] = {"ts": 0.0, "items": None}
_tickers: dict[str, Any] = {"ts": 0.0, "map": None}


class MexcError(RuntimeError):
    """MEXC is unavailable, returned invalid data, or rejected a request."""


def _load_dotenv() -> None:
    """Load MEXC credentials from a local .env file without overriding env vars."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                value = value.strip().strip("\"'")
                if name in {"MEXC_API_KEY", "MEXC_API_SECRET"}:
                    os.environ.setdefault(name, value)
    except OSError:
        pass


def credentials_configured() -> bool:
    _load_dotenv()
    return bool(os.getenv("MEXC_API_KEY") and os.getenv("MEXC_API_SECRET"))


def normalize_symbol(value: str) -> str:
    text = (value or "").strip().upper().replace("/", "_").replace("-", "_")
    if not text:
        return ""
    if "_" in text:
        return text
    return (text[:-4] + "_USDT") if text.endswith("USDT") else text + "_USDT"


def _request(path: str, *, params: dict | None = None, private: bool = False,
             method: str = "GET", body: Any = None) -> Any:
    _load_dotenv()
    method = method.upper()
    if method not in {"GET", "POST", "DELETE"}:
        raise MexcError("Неподдерживаемый метод запроса к MEXC.")
    params = {key: value for key, value in (params or {}).items() if value is not None}
    headers = {"Language": "en-US"}
    request_params = sorted(params.items())
    body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""
    if method != "GET":
        headers["Content-Type"] = "application/json"
    if private:
        api_key = os.getenv("MEXC_API_KEY", "")
        api_secret = os.getenv("MEXC_API_SECRET", "")
        if not api_key or not api_secret:
            raise MexcError(
                "Для расчёта с данными аккаунта укажите MEXC_API_KEY и "
                "MEXC_API_SECRET в локальном файле .env. Для запуска сетки ключу "
                "также потребуется право Order Placing."
            )
        timestamp = str(int(time.time() * 1000))
        param_string = ("&".join(f"{key}={value}" for key, value in request_params)
                        if method == "GET" else body_text)
        message = f"{api_key}{timestamp}{param_string}".encode("utf-8")
        signature = hmac.new(api_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        headers.update({
            "ApiKey": api_key,
            "Request-Time": timestamp,
            "Signature": signature,
        })

    try:
        if method == "GET":
            response = requests.get(
                BASE_URL + path, params=request_params, headers=headers, timeout=HTTP_TIMEOUT
            )
        else:
            response = requests.request(
                method, BASE_URL + path, params=request_params or None,
                data=body_text or None, headers=headers, timeout=HTTP_TIMEOUT
            )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MexcError(f"Не удалось получить данные MEXC: {exc}") from exc

    if isinstance(payload, dict):
        if payload.get("success") is False:
            detail = payload.get("message") or payload.get("msg") or payload.get("code")
            raise MexcError(f"MEXC отклонила запрос: {detail}")
        return payload.get("data", payload)
    return payload


def load_contracts(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _contracts["items"] is not None and now - _contracts["ts"] < CONTRACT_TTL:
        return _contracts["items"]

    raw = _request("/api/v1/contract/detail/country")
    if isinstance(raw, dict):
        if raw.get("symbol"):
            raw = [raw]
        else:
            raw = raw.get("contracts") or raw.get("list") or raw.get("items")
    if not isinstance(raw, list):
        raise MexcError("MEXC вернула неизвестный формат списка контрактов.")
    items = []
    for contract in raw or []:
        symbol = str(contract.get("symbol") or "")
        if (not symbol or contract.get("quoteCoin") != "USDT"
                or contract.get("isHidden") or contract.get("state", 0) != 0
                or contract.get("futureType", 1) != 1):
            continue
        public_max = float(contract.get("maxLeverage") or 0)
        country_max = float(contract.get("countryConfigContractMaxLeverage") or 0)
        max_leverage = min(public_max, country_max) if country_max > 0 else public_max
        items.append({
            **contract,
            "symbol": symbol,
            "base": contract.get("baseCoin") or symbol.split("_")[0],
            "max_leverage": max_leverage,
            "price_scale": int(contract.get("priceScale") or 4),
        })
    items.sort(key=lambda item: item["symbol"])
    _contracts.update(ts=now, items=items)
    return items


def contract_info(symbol: str) -> dict:
    symbol = normalize_symbol(symbol)
    for contract in load_contracts():
        if contract["symbol"] == symbol:
            return contract
    raise MexcError(f"Контракт {symbol} не найден в данных MEXC.")


def ticker_snapshot(force: bool = False) -> dict:
    now = time.time()
    if not force and _tickers["map"] is not None and now - _tickers["ts"] < TICKER_TTL:
        return {"map": _tickers["map"], "ts": _tickers["ts"], "fresh": True}
    try:
        raw = _request("/api/v1/contract/ticker")
        if isinstance(raw, dict):
            raw = [raw]
        result = {}
        for item in raw or []:
            symbol = item.get("symbol")
            if not symbol:
                continue
            result[symbol] = {
                "last": _number(item.get("lastPrice"), 0.0),
                "fair": _number(item.get("fairPrice"), 0.0),
                "funding_rate": _number(item.get("fundingRate")),
                "change": (_number(item.get("riseFallRate"), 0.0) or 0.0) * 100,
                "volume": _number(item.get("amount24"), 0.0) or 0.0,
                "ts": _number(item.get("timestamp"), 0.0),
            }
        _tickers.update(ts=now, map=result)
        return {"map": result, "ts": now, "fresh": True}
    except MexcError:
        if _tickers["map"] is not None:
            return {"map": _tickers["map"], "ts": _tickers["ts"], "fresh": False}
        raise


def all_tickers() -> dict:
    return ticker_snapshot()["map"]


def search(query: str, limit: int = 120) -> list[dict]:
    contracts = load_contracts()
    tickers = all_tickers()
    volume = lambda contract: -((tickers.get(contract["symbol"]) or {}).get("volume") or 0.0)
    query = normalize_symbol(query).replace("_USDT", "") if query else ""
    if not query:
        return sorted(contracts, key=volume)[:limit]
    starts = sorted((item for item in contracts if item["base"].startswith(query)), key=volume)
    contains = sorted((item for item in contracts if not item["base"].startswith(query)
                       and query in item["symbol"]), key=volume)
    return (starts + contains)[:limit]


def _private_get(path: str, params: dict | None = None) -> Any:
    return _request(path, params=params, private=True)


def _private_post(path: str, body: Any) -> Any:
    return _request(path, private=True, method="POST", body=body)


def best_ask(symbol: str) -> float:
    symbol = normalize_symbol(symbol)
    book = _request(f"/api/v1/contract/depth/{symbol}", params={"limit": 5})
    asks = book.get("asks") if isinstance(book, dict) else None
    if not isinstance(asks, list) or not asks:
        raise MexcError("MEXC не вернула стакан заявок для этой пары.")
    prices = []
    for row in asks:
        value = row.get("price") if isinstance(row, dict) else (row[0] if row else None)
        price = _number(value)
        if price is not None and price > 0:
            prices.append(price)
    if not prices:
        raise MexcError("В стакане MEXC отсутствует корректная цена продажи.")
    return min(prices)


def position_mode() -> int:
    value = _private_get("/api/v1/private/position/position_mode")
    if isinstance(value, dict):
        value = value.get("positionMode", value.get("mode"))
    try:
        mode = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MexcError("MEXC не вернула режим позиции аккаунта.") from exc
    if mode not in (1, 2):
        raise MexcError("MEXC вернула неизвестный режим позиции.")
    return mode


def place_order(order: dict) -> Any:
    return _private_post("/api/v1/private/order/create", order)


def order_by_id(order_id: str) -> Any:
    return _private_get(f"/api/v1/private/order/get/{order_id}")


def order_by_external(symbol: str, external_oid: str) -> Any:
    symbol = normalize_symbol(symbol)
    return _private_get(f"/api/v1/private/order/external/{symbol}/{external_oid}")


def cancel_orders(order_ids: list[str]) -> Any:
    ids = [int(order_id) for order_id in order_ids]
    return _private_post("/api/v1/private/order/cancel", {"orderIds": ids})


def _rows(payload: Any, name: str) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("list", "resultList", "orders", "rows", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise MexcError(f"MEXC вернула неизвестный формат {name}.")


def _private_pages(path: str, *, page_key: str, page_size_key: str,
                   params: dict | None = None) -> list[dict]:
    result = []
    for page in range(1, ORDER_PAGE_LIMIT + 1):
        query = dict(params or {})
        query[page_key] = page
        query[page_size_key] = ORDER_PAGE_SIZE
        current = _rows(_private_get(path, query), "списка ордеров")
        result.extend(current)
        if len(current) < ORDER_PAGE_SIZE:
            return result
    raise MexcError("Слишком много активных ордеров MEXC; безопасный расчёт остановлен.")


def risk_context(symbol: str) -> dict:
    """Fetch fresh exchange and account inputs needed by the grid risk estimate."""
    symbol = normalize_symbol(symbol)
    contracts = load_contracts()
    contract_map = {item["symbol"]: item for item in contracts}
    contract = contract_map.get(symbol)
    if not contract:
        raise MexcError(f"Контракт {symbol} не найден в данных MEXC.")
    fee = _private_get("/api/v1/private/account/tiered_fee_rate/v2", {"symbol": symbol})
    assets = _private_get("/api/v1/private/account/assets")
    positions = _private_get("/api/v1/private/position/open_positions")
    risk_limits = _private_get("/api/v1/private/account/risk_limit")
    leverage = _private_get("/api/v1/private/position/leverage", {"symbol": symbol})
    orders = _private_pages(
        "/api/v1/private/order/list/open_orders",
        page_key="page_num", page_size_key="page_size",
    )
    now_ms = int(time.time() * 1000)
    plan_orders = _private_pages(
        "/api/v1/private/planorder/list/orders",
        page_key="page_num", page_size_key="page_size",
        params={"states": "1", "start_time": 0, "end_time": now_ms},
    )
    stop_orders = _rows(
        _private_get("/api/v1/private/stoporder/open_orders"), "условных TP/SL-ордеров"
    )
    trailing_orders = _private_pages(
        "/api/v1/private/trackorder/list/orders",
        page_key="pageIndex", page_size_key="pageSize",
        params={"states": "0,1"},
    )

    positions = _rows(positions, "открытых позиций")
    assets = _rows(assets, "активов аккаунта")
    if isinstance(leverage, dict):
        leverage = leverage.get("list") or leverage.get("resultList") or leverage.get("data") or []

    funding_by_symbol = {}
    risk_limits_by_symbol = risk_limits if isinstance(risk_limits, dict) else {symbol: risk_limits}
    leverage_settings_by_symbol = {symbol: leverage}
    for position in positions or []:
        if int(position.get("state") or 1) != 1:
            continue
        other_symbol = normalize_symbol(position.get("symbol", ""))
        if other_symbol and other_symbol not in funding_by_symbol:
            try:
                funding_by_symbol[other_symbol] = _request(
                    f"/api/v1/contract/funding_rate/{other_symbol}"
                )
            except MexcError:
                # The risk calculator will fail closed if an open position lacks required data.
                funding_by_symbol[other_symbol] = None
        if other_symbol:
            leverage_settings_by_symbol.setdefault(other_symbol, [])

    market = _request(f"/api/v1/contract/fair_price/{symbol}")
    funding = _request(f"/api/v1/contract/funding_rate/{symbol}")
    funding_by_symbol[symbol] = funding

    return {
        "symbol": symbol,
        "contract": contract,
        "market": market or {},
        "fair_price": _number((market or {}).get("fairPrice")),
        "funding": funding or {},
        "fee": fee or {},
        "assets": assets or [],
        "positions": positions or [],
        "orders": orders,
        "plan_orders": plan_orders,
        "stop_orders": stop_orders,
        "trailing_orders": trailing_orders,
        "risk_limits": risk_limits or {},
        "risk_limits_by_symbol": risk_limits_by_symbol,
        "leverage_settings": leverage or [],
        "leverage_settings_by_symbol": leverage_settings_by_symbol,
        "contracts": contract_map,
        "funding_by_symbol": funding_by_symbol,
        "fetched_at": int(time.time() * 1000),
    }


def _number(value, default=None):
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError):
        return default
