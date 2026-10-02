"""Local MEXC averaging-grid calculator web app."""

import threading
import math
import time
import uuid
import webbrowser

from flask import Flask, jsonify, render_template, request

import mexc
from grid import (API_MAKER_FEE_FLOOR, API_TAKER_FEE_FLOOR, GridError,
                  calculate_exchange_grid)

app = Flask(__name__)
app.json.ensure_ascii = False
_grid_launch_lock = threading.Lock()


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/pairs")
def api_pairs():
    try:
        found = mexc.search(request.args.get("q", ""), limit=120)
        prices = mexc.all_tickers()
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify([{
        "symbol": c["symbol"], "base": c["base"],
        "leverage": c["max_leverage"],
        "price": (prices.get(c["symbol"]) or {}).get("fair"),
        "last": (prices.get(c["symbol"]) or {}).get("last"),
        "funding": (prices.get(c["symbol"]) or {}).get("funding_rate"),
        "change": (prices.get(c["symbol"]) or {}).get("change"),
        "volume": (prices.get(c["symbol"]) or {}).get("volume"),
        "places": c["price_scale"],
    } for c in found])


@app.route("/api/prices")
def api_prices():
    wanted = [mexc.normalize_symbol(x) for x in request.args.get("symbols", "").split(",")]
    wanted = [x for x in wanted if x][:300]
    try:
        snap = mexc.ticker_snapshot()
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502
    prices = {}
    for symbol in wanted:
        tick = snap["map"].get(symbol)
        if tick:
            prices[symbol] = {"price": tick["fair"] or tick["last"],
                              "last": tick["last"], "change": tick["change"],
                              "volume": tick["volume"]}
    return jsonify({"ts": snap["ts"], "fresh": snap["fresh"], "prices": prices})


@app.route("/api/context")
def api_context():
    symbol = mexc.normalize_symbol(request.args.get("symbol", ""))
    if not symbol:
        return jsonify({"error": "Выберите фьючерсную пару."}), 400
    try:
        context = mexc.risk_context(symbol)
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502

    account = next((item for item in context["assets"]
                    if str(item.get("currency", "")).upper() == "USDT"), {})
    settings = context["leverage_settings"]
    if isinstance(settings, dict):
        settings = settings.get("list") or settings.get("data") or []
    long_settings = [item for item in settings
                     if int(item.get("positionType") or 1) == 1]
    current_modes = {
        int(position.get("openType") or 0)
        for position in context["positions"]
        if int(position.get("state") or 1) == 1
        and str(position.get("symbol", "")) == symbol
    }
    preferred_mode = next(iter(current_modes)) if len(current_modes) == 1 else 1
    long_setting = next((item for item in long_settings
                         if int(item.get("openType") or 0) == preferred_mode),
                        long_settings[0] if long_settings else {})
    risk_limits = context["risk_limits"]
    if isinstance(risk_limits, dict):
        risk_limits = risk_limits.get(symbol, [])
    if isinstance(risk_limits, dict):
        risk_limits = risk_limits.get("list") or [risk_limits]
    tier_maxima = [float(item["maxLeverage"]) for item in risk_limits or []
                   if int(item.get("positionType") or 1) == 1
                   and int(item.get("openType") or long_setting.get("openType") or 1)
                   == int(long_setting.get("openType") or 1)
                   and item.get("maxLeverage") is not None]
    funding = context["funding"]
    fee = context["fee"]
    return jsonify({
        "symbol": symbol,
        "fair_price": context["fair_price"],
        "funding_rate": funding.get("fundingRate"),
        "max_funding_rate": funding.get("maxFundingRate"),
        "collect_cycle": funding.get("collectCycle"),
        "next_settle_time": funding.get("nextSettleTime"),
        "maker_fee": max(_number(fee.get("realMakerFee", fee.get("originalMakerFee"))),
                         API_MAKER_FEE_FLOOR),
        "taker_fee": max(_number(fee.get("realTakerFee", fee.get("originalTakerFee"))),
                         API_TAKER_FEE_FLOOR),
        "available": account.get("availableOpen", account.get("availableBalance")),
        "leverage": long_setting.get("leverage"),
        "max_leverage": min(
            _number(context["contract"].get("max_leverage")),
            _number(long_setting.get("maxLeverageView"), context["contract"].get("max_leverage", 0)),
            _number(fee.get("maxLeverage"), context["contract"].get("max_leverage", 0)),
            *tier_maxima,
        ),
        "open_type": long_setting.get("openType"),
        "positions": len([p for p in context["positions"]
                          if int(p.get("state", 1)) == 1]),
        "fetched_at": context["fetched_at"],
    })


@app.route("/api/grid", methods=["POST"])
def api_grid():
    data = request.get_json(silent=True) or {}
    try:
        mode = data.get("mode", "leverage")
        leverage_text = str(data.get("leverage", "")).strip()
        leverage = (int(float(leverage_text.replace(",", ".")))
                    if mode == "leverage" and leverage_text else None)
        target = float(str(data["target_liq"]).replace(",", ".")) if mode == "target" else None
        symbol = mexc.normalize_symbol(data.get("symbol", ""))
        context = mexc.risk_context(symbol)
        result = calculate_exchange_grid(
            data.get("p1", ""), context=context, leverage=leverage, target=target,
            hours_until_step4=data.get("hours_until_step4", 24))
        result["rows"] = [{
            **row,
            "price": round(row["price"], result["places"]),
            "liq": round(row["liq"], result["places"]),
            "avg": round(row["avg"], result["places"]),
            "pct": round(row["pct"], 2),
            "contracts": round(row["contracts"], result["volume_places"]),
        } for row in result["rows"]]
        return jsonify(result)
    except GridError as exc:
        return jsonify({"error": str(exc)}), 400
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502
    except (KeyError, TypeError, ValueError, OverflowError):
        return jsonify({"error": "Проверьте введённые числа."}), 400


def _grid_order_id(response, symbol: str, external_oid: str) -> str:
    if isinstance(response, dict):
        order_id = response.get("orderId") or response.get("id")
        if not order_id and isinstance(response.get("data"), dict):
            order_id = response["data"].get("orderId") or response["data"].get("id")
        if order_id:
            return str(order_id)
    try:
        found = mexc.order_by_external(symbol, external_oid)
    except mexc.MexcError as exc:
        raise mexc.MexcError(
            f"MEXC не подтвердила отправку ордера {external_oid}. Не нажимайте запуск повторно, "
            "пока не проверите открытые ордера на бирже."
        ) from exc
    if isinstance(found, dict) and found.get("orderId"):
        return str(found["orderId"])
    raise mexc.MexcError(
        f"MEXC приняла запрос {external_oid} без номера ордера. Проверьте Futures ордера на бирже."
    )


def _submit_grid_order(payload: dict, symbol: str, external_oid: str) -> str:
    try:
        response = mexc.place_order(payload)
    except mexc.MexcError as submit_error:
        try:
            response = mexc.order_by_external(symbol, external_oid)
        except mexc.MexcError as lookup_error:
            raise mexc.MexcError(
                f"Статус ордера {external_oid} не подтверждён. Проверьте Futures ордера "
                f"на MEXC до повторного запуска. Детали: {submit_error}"
            ) from lookup_error
    return _grid_order_id(response, symbol, external_oid)


def _wait_for_order(order_id: str) -> dict:
    deadline = time.monotonic() + 5.0
    last_error = None
    while time.monotonic() < deadline:
        try:
            order = mexc.order_by_id(order_id)
            if isinstance(order, dict) and int(order.get("state") or 0) in (3, 4, 5):
                return order
        except (mexc.MexcError, TypeError, ValueError) as exc:
            last_error = exc
        time.sleep(0.25)
    message = "Не удалось подтвердить исполнение первого ордера; проверьте его статус на MEXC."
    if last_error:
        message += f" Детали: {last_error}"
    raise GridError(message)


def _cancel_own_grid_orders(order_ids: list[str]) -> str:
    if not order_ids:
        return ""
    try:
        response = mexc.cancel_orders(order_ids)
    except mexc.MexcError as exc:
        return f" Не удалось отменить лимитные ордера {', '.join(order_ids)}: {exc}"
    rows = response if isinstance(response, list) else (
        response.get("data", []) if isinstance(response, dict) else []
    )
    if not isinstance(rows, list) or len(rows) != len(order_ids):
        return (f" MEXC не подтвердила отмену всех ордеров {', '.join(order_ids)}. "
                "Проверьте их статус на бирже.")
    failures = []
    for row in rows:
        try:
            if not isinstance(row, dict) or int(row.get("errorCode")) != 0:
                failures.append(row)
        except (TypeError, ValueError, OverflowError):
            failures.append(row)
    if failures:
        failed_ids = ", ".join(
            str(row.get("orderId", "?")) if isinstance(row, dict) else "?"
            for row in failures
        )
        return f" Не удалось подтвердить отмену ордеров: {failed_ids}. Проверьте MEXC."
    return " Уже выставленные этой сеткой лимитные ордера отменены."


@app.route("/api/grid/start", methods=["POST"])
def api_grid_start():
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify({"error": "Запуск доступен только с этого компьютера."}), 403
    if not _grid_launch_lock.acquire(blocking=False):
        return jsonify({"error": "Сетка уже запускается."}), 409

    placed_limit_ids = []
    first_order_id = None
    first_external = None
    first_position_opened = False
    symbol = ""
    try:
        data = request.get_json(silent=True) or {}
        symbol = mexc.normalize_symbol(data.get("symbol", ""))
        if not symbol:
            raise GridError("Выберите фьючерсную пару.")

        context = mexc.risk_context(symbol)
        contract = context["contract"]
        if contract.get("apiAllowed") is False:
            raise GridError("MEXC отключила API-торговлю для этой пары.")
        if any(int(position.get("state") or 1) == 1
               and str(position.get("symbol", "")) == symbol
               for position in context.get("positions", [])):
            raise GridError("По этой паре уже есть позиция. Запуск сетки остановлен.")

        price_unit = float(contract.get("priceUnit") or 0)
        contract_size = float(contract.get("contractSize") or 0)
        volume_unit = float(contract.get("volUnit") or 0)
        if not all(math.isfinite(value) and value > 0
                   for value in (price_unit, contract_size, volume_unit)):
            raise GridError("В данных MEXC отсутствует шаг цены или объёма контракта.")
        ask = mexc.best_ask(symbol)
        first_cap = math.ceil((ask + price_unit) / price_unit - 1e-10) * price_unit

        mode = data.get("mode", "leverage")
        if mode not in {"leverage", "target"}:
            raise GridError("Неизвестный режим расчёта сетки.")
        leverage_text = str(data.get("leverage", "")).strip()
        leverage = (int(float(leverage_text.replace(",", ".")))
                    if mode == "leverage" and leverage_text else None)
        target = (float(str(data["target_liq"]).replace(",", "."))
                  if mode == "target" else None)
        result = calculate_exchange_grid(
            first_cap, context=context, leverage=leverage, target=target,
            hours_until_step4=data.get("hours_until_step4", 24),
        )
        if result["open_type"] != 1:
            raise GridError(
                "Для ограничения сетки бюджетом 10 USDT включите изолированную маржу "
                "для этой пары в MEXC."
            )
        if result["budget_required"] > min(10.0, result["account_available"]) + 1e-9:
            raise GridError("На фьючерсном счёте недостаточно доступных средств для сетки.")
        first = result["rows"][0]
        if first["contracts"] <= 0 or first["actual_margin"] > 1.0 + 1e-8:
            raise GridError("На этой паре нельзя открыть первый шаг с маржой не более 1 USDT.")

        position_mode = mexc.position_mode()
        first_external = uuid.uuid4().hex
        first_payload = {
            "symbol": symbol,
            "price": round(first_cap, result["places"]),
            "vol": round(first["contracts"], result["volume_places"]),
            "leverage": result["leverage"],
            "side": 1,
            "type": 3,
            "openType": 1,
            "positionMode": position_mode,
            "externalOid": first_external,
        }
        first_order_id = _submit_grid_order(first_payload, symbol, first_external)
        first_order = _wait_for_order(first_order_id)
        filled_volume = _number(first_order.get("dealVol"), 0)
        if (int(first_order.get("state") or 0) != 3
                or filled_volume + volume_unit * 1e-7 < first["contracts"]):
            return jsonify({
                "error": "Первый вход исполнился не полностью. Остальные три ордера не выставлены; "
                         "проверьте открытую позицию на MEXC.",
                "first_order_id": first_order_id,
                "filled_volume": filled_volume,
                "requested_volume": first["contracts"],
            }), 409

        fill_price = _number(first_order.get("dealAvgPrice"), 0)
        if fill_price <= 0 or fill_price > first_cap + price_unit * 1e-7:
            raise GridError(
                "Первый вход открыт, но MEXC вернула неожиданную цену исполнения; "
                "лимитки не выставлены."
            )
        first_position_opened = True

        actual_context = mexc.risk_context(symbol)
        position_rows = [position for position in actual_context.get("positions", [])
                         if int(position.get("state") or 1) == 1
                         and str(position.get("symbol", "")) == symbol]
        long_positions = [position for position in position_rows
                          if int(position.get("positionType") or 0) == 1]
        if len(long_positions) != 1 or int(long_positions[0].get("openType") or 0) != 1:
            raise GridError("Первый вход открыт, но MEXC не подтвердила изолированную LONG-позицию; лимитки не выставлены.")
        if int(long_positions[0].get("leverage") or 0) != result["leverage"]:
            raise GridError("Первый вход открыт с другим плечом; лимитки не выставлены.")
        actual_liquidation = _number(long_positions[0].get("liquidatePrice"), 0)
        if actual_liquidation <= 0:
            raise GridError("Первый вход открыт, но MEXC не вернула цену ликвидации; лимитки не выставлены.")

        new_funding = actual_context.get("funding") or {}
        new_stress_rate = max(0.0, _number(new_funding.get("maxFundingRate"), 0))
        if new_stress_rate > result["funding_stress_rate"] + 1e-12:
            raise GridError("Первый вход открыт, но ставка фандинга выросла; лимитки не выставлены.")

        shift = fill_price - first["price"]
        projected_liquidations = [row["liq"] + shift for row in result["rows"]]
        projected_liquidations[0] = actual_liquidation
        limit_prices = []
        for index, row in enumerate(result["rows"][1:], start=1):
            price = math.floor((row["price"] + shift) / price_unit + 1e-10) * price_unit
            if (price <= projected_liquidations[index - 1] + price_unit
                    or (limit_prices and price >= limit_prices[-1])):
                raise GridError(
                    "Первый вход открыт, но после сверки с MEXC один из следующих шагов "
                    "оказался слишком близко к ликвидации; лимитки не выставлены."
                )
            limit_prices.append(price)

        actual_fair = _number(actual_context.get("fair_price"), 0)
        if actual_fair <= actual_liquidation or limit_prices[0] >= actual_fair:
            raise GridError(
                "Первый вход открыт, но цена MEXC уже прошла первый лимитный уровень; "
                "лимитки не выставлены."
            )

        submitted = []
        for index, (row, price) in enumerate(zip(result["rows"][1:], limit_prices), start=2):
            external_oid = uuid.uuid4().hex
            payload = {
                "symbol": symbol,
                "price": round(price, result["places"]),
                "vol": round(row["contracts"], result["volume_places"]),
                "leverage": result["leverage"],
                "side": 1,
                "type": 1,
                "openType": 1,
                "positionMode": position_mode,
                "externalOid": external_oid,
            }
            order_id = _submit_grid_order(payload, symbol, external_oid)
            placed_limit_ids.append(order_id)
            submitted.append({
                "step": index,
                "order_id": order_id,
                "price": price,
                "contracts": row["contracts"],
                "margin": row["actual_margin"],
            })

        return jsonify({
            "ok": True,
            "symbol": symbol,
            "first_order_id": first_order_id,
            "first_fill_price": fill_price,
            "limits": submitted,
            "budget_limit": result["budget_limit"],
            "budget_required": result["budget_required"],
            "budget_margin": result["budget_margin"],
            "budget_entry_fees": result["budget_entry_fees"],
            "budget_funding": result["budget_funding"],
        }), 201
    except GridError as exc:
        cancel_note = _cancel_own_grid_orders(placed_limit_ids)
        message = str(exc)
        if first_position_opened:
            message = f"Первый вход {first_order_id} уже открыт. {message}{cancel_note}"
        elif first_order_id:
            message = f"Статус первого ордера {first_order_id} не подтверждён. Проверьте MEXC. {message}"
        return jsonify({
            "error": message,
            "first_order_id": first_order_id,
            "status_unknown": bool(first_external and not first_order_id),
        }), 400
    except mexc.MexcError as exc:
        cancel_note = _cancel_own_grid_orders(placed_limit_ids)
        message = str(exc)
        if first_position_opened:
            message = f"Первый вход {first_order_id} уже открыт. {message}{cancel_note}"
        elif first_order_id:
            message = f"Статус первого ордера {first_order_id} не подтверждён. Проверьте MEXC. {message}"
        return jsonify({
            "error": message,
            "first_order_id": first_order_id,
            "status_unknown": bool(first_external and not first_order_id),
        }), 502
    except (KeyError, TypeError, ValueError, OverflowError):
        cancel_note = _cancel_own_grid_orders(placed_limit_ids)
        message = "Не удалось подготовить сетку по ответу MEXC."
        if first_position_opened:
            message = f"Первый вход {first_order_id} уже открыт. {message}{cancel_note}"
        elif first_order_id:
            message = f"Статус первого ордера {first_order_id} не подтверждён. Проверьте MEXC. {message}"
        return jsonify({
            "error": message,
            "first_order_id": first_order_id,
            "status_unknown": bool(first_external and not first_order_id),
        }), 502
    finally:
        _grid_launch_lock.release()


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000)
