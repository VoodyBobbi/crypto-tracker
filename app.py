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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
