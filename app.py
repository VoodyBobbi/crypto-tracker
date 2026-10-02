"""Local MEXC averaging-grid calculator web app."""

import threading
import webbrowser

from flask import Flask, jsonify, render_template, request

import mexc
from grid import GridError, calculate_exchange_grid

app = Flask(__name__)
app.json.ensure_ascii = False


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
    long_setting = next((item for item in settings
                         if int(item.get("positionType") or 1) == 1), {})
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
        "maker_fee": fee.get("realMakerFee", fee.get("originalMakerFee")),
        "taker_fee": fee.get("realTakerFee", fee.get("originalTakerFee")),
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


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000)
