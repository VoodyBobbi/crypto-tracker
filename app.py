"""Калькулятор сетки усреднения (LONG) для фьючерсов MEXC.

Одна страница: слева список USDT-фьючерсов, справа таблица расчёта.
Запуск:  python app.py  →  http://127.0.0.1:5000
"""

from flask import Flask, jsonify, render_template, request

import mexc
from grid import (GridError, build_grid, decimals_of, solve_leverage,
                  solve_leverage_exchange)

app = Flask(__name__)
app.json.ensure_ascii = False

# Порог из модели: при MMR ниже 0.15 / L идеализированный расчёт ещё безопасен,
# выше — следующий вход оказывается ниже реальной ликвидации.
SAFE_MMR_FACTOR = 0.15


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/pairs")
def api_pairs():
    """Список USDT-фьючерсов с ценой и суточным изменением."""
    try:
        found = mexc.search(request.args.get("q", ""), limit=120)
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502

    try:
        prices = mexc.all_tickers()
    except mexc.MexcError:
        prices = {}

    rows = []
    for c in found:
        tick = prices.get(c["symbol"]) or {}
        rows.append({
            "symbol": c["symbol"],
            "base": c["base"],
            "leverage": c["max_leverage"],
            "price": tick.get("last"),
            "change": tick.get("change"),
            "places": c["price_scale"],
            "mmr": c["mmr_base"],
        })
    return jsonify(rows)


@app.route("/api/grid", methods=["POST"])
def api_grid():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(calculate(data))
    except GridError as exc:
        return jsonify({"error": str(exc)}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Проверьте числа — что-то введено не так."}), 400


def _number(text):
    return float(str(text or "").strip().replace(",", "."))


def calculate(data):
    p1_raw = str(data.get("p1") or "").strip().replace(",", ".")
    if not p1_raw:
        raise GridError("Нужна цена входа.")
    p1 = float(p1_raw)

    contract = None
    notes = []
    symbol = str(data.get("symbol") or "").strip()
    if symbol:
        try:
            contract = mexc.get_contract(symbol)
        except mexc.MexcError as exc:
            notes.append(f"Биржа не ответила: {exc}")
        else:
            if contract is None:
                raise GridError(
                    f"Пары {mexc.normalize_symbol(symbol)} нет на фьючерсах MEXC."
                )

    # Источник MMR определяется явно. Молчаливого нуля быть не должно.
    mmr_fn = mexc.make_mmr_fn(contract) if contract else None
    mmr_known = mmr_fn is not None
    if contract is not None and not mmr_known:
        notes.append("MEXC не дала ставку поддерживающей маржи для этой пары.")

    # Биржа исполняет шаг не дробными монетами: из внесённой суммы уходит
    # комиссия за открытие, остаток делится на стоимость целого контракта,
    # дробь отбрасывается. Учитываем это, когда метаданные пары известны.
    lot = contract["contract_size"] if contract else None
    fee = (contract["taker_fee"] or 0.0) if contract else 0.0

    max_lev = contract["max_leverage"] if contract else None

    if data.get("mode") == "leverage":
        leverage = int(round(_number(data.get("leverage"))))
        if leverage < 2:
            raise GridError("Плечо должно быть от 2x.")
        if max_lev and leverage > max_lev:
            raise GridError(f"На этой паре плечо максимум {max_lev:g}x.")
    else:
        target = _number(data.get("target_liq"))
        if lot:
            _, leverage = solve_leverage_exchange(
                p1, target, mmr_fn=mmr_fn, max_leverage=max_lev, lot=lot, fee=fee)
        else:
            _, leverage = solve_leverage(
                p1, target, mmr_fn=mmr_fn, max_leverage=max_lev)

    rows = build_grid(p1, leverage, mmr_fn=mmr_fn, lot=lot, fee=fee)

    places = contract["price_scale"] if contract else max(decimals_of(p1_raw), 4)
    mmr_step1 = (1 - rows[0]["k"]) / leverage

    # Идеализированный расчёт безопасен не всегда — говорим, до какого MMR.
    if not mmr_known:
        limit = SAFE_MMR_FACTOR / leverage * 100
        notes.append(
            f"Расчёт без резерва биржи. Он верен, только если MMR монеты "
            f"меньше {limit:.3f}%. Выше — добор встанет ниже ликвидации."
        )

    if contract and contract["min_vol"]:
        small = [r["step"] for r in rows
                 if r["contracts"] is not None and r["contracts"] < contract["min_vol"]]
        if small:
            steps = ", ".join(str(n) for n in small)
            notes.append(
                f"Шаг {steps}: количество меньше минимального ордера биржи "
                f"({contract['min_vol']:g} контр.) — такой ордер не пройдёт."
            )

    spent = sum(r["margin_used"] + r["fee"] for r in rows)
    if lot and spent < 9.9:
        notes.append(
            f"Из 10 USDT биржа задействует {spent:.2f} — остальное не влезает "
            "в целое число контрактов. Точки входа посчитаны по фактическим суммам."
        )

    return {
        "leverage": leverage,
        "places": places,
        "mmr": {
            "known": mmr_known,
            "value": mmr_step1 if mmr_known else None,
            "tier": mexc.tier_for_volume(contract, rows[0]["cum_coins"]) if mmr_known else None,
            "source": "MEXC" if mmr_known else "нет данных",
        },
        "exchange_lots": bool(lot),
        "qty_places": decimals_of(repr(lot).rstrip("0").rstrip(".")) if lot else 4,
        "notes": notes,
        "rows": [
            {
                "step": r["step"],
                "price": round(mexc.round_to_tick(r["price"], contract), places),
                "margin": r["margin"],
                "margin_used": round(r["margin_used"], 4),
                "coins": r["coins"],
                "contracts": r["contracts"],
                "liq": round(mexc.round_to_tick(r["liq"], contract), places),
                "pct": round(r["pct_path"], 2),
            }
            for r in rows
        ],
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
