"""Crypto Tracker — Flask web application.

Features:
- Dark, responsive UI showing the TOP-10 cryptocurrencies (CoinGecko API).
- User registration / login / logout with server-side session auth.
- Users are stored locally in a JSON file with hashed passwords.
- The dashboard auto-refreshes market data every 30 seconds.
"""

import json
import os
import re
import time
from functools import wraps

import requests
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import mexc
from grid import GridError, build_grid, decimals_of, solve_leverage

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_PARAMS = {
    "vs_currency": "usd",
    "order": "market_cap_desc",
    "per_page": 10,
    "page": 1,
    "sparkline": "false",
    "price_change_percentage": "24h",
}

# Simple in-memory cache to stay within CoinGecko's free rate limits.
_CACHE_TTL_SECONDS = 25
_cache = {"data": None, "ts": 0.0}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.json.ensure_ascii = False  # кириллица в ответах API читается как есть


# --------------------------------------------------------------------------- #
# User storage helpers
# --------------------------------------------------------------------------- #
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        users = load_users()
        error = None
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            error = "Username must be 3-32 chars (letters, digits, . _ -)."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif username.lower() in {u.lower() for u in users}:
            error = "This username is already taken."

        if error:
            flash(error, "error")
            return render_template("register.html", username=username)

        users[username] = {"password": generate_password_hash(password)}
        save_users(users)
        session["user"] = username
        flash("Account created. Welcome!", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        users = load_users()
        # Case-insensitive username lookup.
        match = next((u for u in users if u.lower() == username.lower()), None)
        if match and check_password_hash(users[match]["password"], password):
            session["user"] = match
            flash("Signed in successfully.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")
        return render_template("login.html", username=username)

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=session["user"])


@app.route("/api/coins")
@login_required
def api_coins():
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL_SECONDS:
        return jsonify(_cache["data"])

    try:
        resp = requests.get(COINGECKO_URL, params=COINGECKO_PARAMS, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as exc:
        # Serve stale cache if we have it, otherwise report the error.
        if _cache["data"] is not None:
            return jsonify(_cache["data"])
        return jsonify({"error": f"Failed to fetch market data: {exc}"}), 502

    coins = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "symbol": (c.get("symbol") or "").upper(),
            "image": c.get("image"),
            "price": c.get("current_price"),
            "change_24h": c.get("price_change_percentage_24h"),
            "market_cap": c.get("market_cap"),
            "rank": c.get("market_cap_rank"),
        }
        for c in raw
    ]
    payload = {"coins": coins, "updated_at": now}
    _cache["data"] = payload
    _cache["ts"] = now
    return jsonify(payload)


# --------------------------------------------------------------------------- #
# Калькулятор сетки: страница и API
# --------------------------------------------------------------------------- #
@app.route("/calc")
def calc():
    """Калькулятор доступен без регистрации — это локальный инструмент."""
    return render_template("calc.html")


@app.route("/api/mexc/search")
def api_mexc_search():
    """Список фьючерсных контрактов для панели выбора пары."""
    query = request.args.get("q", "")
    try:
        limit = max(1, min(int(request.args.get("limit", 60)), 300))
        results = mexc.search(query, limit=limit)
        _, cached_at, fresh = mexc.load_contracts()
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502
    except ValueError:
        return jsonify({"error": "Некорректный параметр limit."}), 400

    return jsonify({
        "query": query,
        "cached_at": cached_at,
        "fresh": fresh,
        "count": len(results),
        "contracts": [
            {
                "symbol": c["symbol"],
                "base": c["base"],
                "quote": c["quote"],
                "display_name": c["display_name"],
                "max_leverage": c["max_leverage"],
                "price_scale": c["price_scale"],
                "is_hot": c["is_hot"],
                "is_new": c["is_new"],
            }
            for c in results
        ],
    })


@app.route("/api/mexc/contract/<symbol>")
def api_mexc_contract(symbol):
    """Метаданные контракта, текущая цена и ставка фандинга."""
    try:
        contract = mexc.get_contract(symbol)
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502
    if not contract:
        return jsonify({"error": f"Контракт {symbol} не найден."}), 404

    ticker = funding = None
    try:
        ticker = mexc.get_ticker(symbol)
    except mexc.MexcError:
        pass
    try:
        funding = mexc.get_funding_rate(symbol)
    except mexc.MexcError:
        pass

    return jsonify({"contract": contract, "ticker": ticker, "funding": funding})


@app.route("/api/mexc/raw/<symbol>")
def api_mexc_raw(symbol):
    """Сырой JSON контракта — шаг 2 из порядка сборки (п. 2.6 ТЗ)."""
    try:
        data = mexc.fetch_raw_contract(symbol)
    except mexc.MexcError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify(data)


def _grid_payload(form):
    """Разбирает вход, строит сетку, собирает ответ для интерфейса."""
    p1_raw = (form.get("p1") or "").strip().replace(",", ".")
    if not p1_raw:
        raise GridError("Укажите цену входа первого шага.")
    p1 = float(p1_raw)

    symbol = (form.get("symbol") or "").strip().upper() or None
    contract = None
    warnings = []
    if symbol:
        try:
            contract = mexc.get_contract(symbol)
        except mexc.MexcError as exc:
            warnings.append(f"Метаданные MEXC недоступны: {exc}")

    # --- k: идеализация, ручная калибровка или тиры MMR -------------------- #
    k_mode = form.get("k_mode") or "ideal"
    k_const = 1.0
    mmr_fn = None

    if k_mode == "manual":
        k_const = float(str(form.get("k_manual") or 1).replace(",", "."))
        if k_const > 1:                       # введено в процентах
            k_const /= 100
    elif k_mode == "mexc":
        if contract:
            mmr_fn = mexc.make_mmr_fn(contract)
        else:
            k_mode = "ideal"
            warnings.append("Тиры MMR недоступны без пары — считаю с k = 1.0.")

    # --- Режим A (подбор плеча) или Режим Б (плечо задано) ----------------- #
    mode = form.get("mode") or "target"
    max_lev = contract["max_leverage"] if contract else None
    leverage_exact = None
    target_liq = None

    if mode == "target":
        target_raw = (form.get("target_liq") or "").strip().replace(",", ".")
        if not target_raw:
            raise GridError("Укажите целевую ликвидацию четвёртого шага.")
        target_liq = float(target_raw)
        leverage_exact, leverage = solve_leverage(
            p1, target_liq, k=k_const, mmr_fn=mmr_fn, max_leverage=max_lev
        )
    else:
        lev_raw = (form.get("leverage") or "").strip().replace(",", ".")
        if not lev_raw:
            raise GridError("Укажите плечо.")
        leverage = int(round(float(lev_raw)))

    if max_lev and leverage > max_lev:
        warnings.append(
            f"Нужно плечо {leverage}x, контракт допускает максимум {max_lev:g}x. "
            "Цель в этой паре недостижима."
        )

    rows = build_grid(p1, leverage, k=k_const, mmr_fn=mmr_fn)

    # С парой разрядность диктует биржа (priceScale). Без пары — разрядность
    # введённого P1, но не меньше 4 знаков: иначе «100» схлопнет всю таблицу
    # в целые числа и разойдётся с контрольным примером из п. 1.7 ТЗ.
    places = contract["price_scale"] if contract else max(decimals_of(p1_raw), 4)
    taker = contract["taker_fee"] if contract else 0.0

    out_rows = []
    for row in rows:
        out_rows.append({
            "step": row["step"],
            "price": round(row["price"], places),
            "price_tick": round(mexc.round_to_tick(row["price"], contract), places),
            "margin": row["margin"],
            "leverage": leverage,
            "liq": round(row["liq"], places),
            "pct_path": row["pct_path"],
            "k": row["k"],
            "avg": round(row["avg"], places),
            "coins": row["coins"],
            "cum_coins": row["cum_coins"],
            "cum_margin": row["cum_margin"],
            "position_value": row["cum_coins"] * row["price"],
            "fee": row["margin"] * leverage * taker,
            "mmr": mexc.mmr_for_volume(contract, row["cum_coins"]) if contract else None,
            "tier": mexc.tier_for_volume(contract, row["cum_coins"]) if contract else None,
        })

    if contract:
        step1_vol = rows[0]["coins"] / (contract["contract_size"] or 1.0)
        if step1_vol < contract["min_vol"]:
            warnings.append(
                f"Шаг 1 даёт {step1_vol:.4f} контракта при минимуме "
                f"{contract['min_vol']:g} — первый ордер не пройдёт."
            )

    return {
        "mode": mode,
        "k_mode": k_mode,
        "symbol": symbol,
        "p1": p1,
        "places": places,
        "leverage": leverage,
        "leverage_exact": leverage_exact,
        "target_liq": target_liq,
        "liq_final": out_rows[-1]["liq"],
        "liq_delta": (out_rows[-1]["liq"] - target_liq) if target_liq else None,
        "total_margin": rows[-1]["cum_margin"],
        "total_coins": rows[-1]["cum_coins"],
        "total_fee": sum(r["fee"] for r in out_rows),
        "rows": out_rows,
        "contract": contract,
        "warnings": warnings,
    }


@app.route("/api/grid", methods=["POST"])
def api_grid():
    form = request.get_json(silent=True) or request.form
    try:
        return jsonify(_grid_payload(form))
    except GridError as exc:
        return jsonify({"error": str(exc)}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Проверьте числа во входных полях."}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
