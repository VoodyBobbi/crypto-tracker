"""Local MEXC averaging-grid calculator web app."""

import threading
import webbrowser

from flask import Flask, jsonify, render_template, request

import mexc
from grid import GridError, calculate as calculate_grid

app = Flask(__name__)
app.json.ensure_ascii = False


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
        "price": (prices.get(c["symbol"]) or {}).get("last"),
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
            prices[symbol] = {"price": tick["last"], "change": tick["change"],
                              "volume": tick["volume"]}
    return jsonify({"ts": snap["ts"], "fresh": snap["fresh"], "prices": prices})


@app.route("/api/grid", methods=["POST"])
def api_grid():
    data = request.get_json(silent=True) or {}
    try:
        mode = data.get("mode", "leverage")
        leverage = int(float(str(data["leverage"]).replace(",", "."))) if mode == "leverage" else None
        target = float(str(data["target_liq"]).replace(",", ".")) if mode == "target" else None
        mmr_pct = float(str(data["mmr_pct"]).replace(",", ".")) if data.get("mmr_pct", "") != "" else None
        liq1 = float(str(data["liq1"]).replace(",", ".")) if data.get("liq1", "") != "" else None
        funding_pct = float(str(data.get("funding_pct", "0")).replace(",", "."))
        funding_count = int(data.get("funding_count") or 0)
        result = calculate_grid(
            data.get("p1", ""), leverage=leverage, target=target,
            mmr_pct=mmr_pct, liq1=liq1, funding_pct=funding_pct,
            funding_count=funding_count)
        result["rows"] = [{**row, "price": round(row["price"], result["places"]),
                           "liq": round(row["liq"], result["places"]),
                           "pct": round(row["pct"], 2)} for row in result["rows"]]
        return jsonify(result)
    except GridError as exc:
        return jsonify({"error": str(exc)}), 400
    except (KeyError, TypeError, ValueError, OverflowError):
        return jsonify({"error": "Проверьте введённые числа."}), 400


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000)
