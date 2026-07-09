# Crypto Tracker

A modern, dark-themed web application that shows the **TOP-10 cryptocurrencies** in
real time, with user registration/login and a personal dashboard. Built with
**Python + Flask** and the free public **[CoinGecko API](https://www.coingecko.com/en/api)**.

## Features

- Live TOP-10 cryptocurrencies ranked by market capitalization
- For each coin: logo, name, symbol, current price, 24h change and market cap
- Data **auto-refreshes every 30 seconds** (no page reload needed)
- User **registration and login** with server-side session auth
- Passwords are **hashed** (werkzeug) — never stored in plain text
- Users are stored locally in `users.json` (auto-created, git-ignored)
- Server-side caching of market data (25s TTL) to respect CoinGecko's free-tier
  rate limits, with graceful fallback to stale data / a clean error response
  if the API is unreachable
- Flash messages for registration/login feedback
- Fully responsive UI with a modern dark theme

## Project structure

```
crypto-tracker/
├── app.py              # Flask application (routes, auth, CoinGecko proxy)
├── requirements.txt    # Python dependencies
├── README.md
├── templates/          # Jinja2 HTML templates
│   ├── base.html        # shared layout, nav, flash messages
│   ├── index.html        # landing page
│   ├── login.html
│   ├── register.html
│   └── dashboard.html    # live top-10 table
└── static/
    ├── css/style.css
    └── js/dashboard.js   # fetches /api/coins and auto-refreshes
```

## Requirements

- Python 3.9+ (tested on Python 3.10/3.12)

## Setup & run

```bash
# 1. (optional but recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. run the app
python app.py
```

Then open the app in your browser:

```
http://127.0.0.1:5000
```

## Usage

1. Open `http://127.0.0.1:5000`.
2. Click **Sign up** and create an account (username 3-32 chars; password 6+ chars).
3. You are redirected to the **Dashboard** showing the live TOP-10 coins.
4. The table refreshes automatically every 30 seconds; use **Refresh** to update now.
5. Use **Log out** to end your session.

## Configuration

- `SECRET_KEY` — set this environment variable in production to secure sessions:

  ```bash
  export SECRET_KEY="your-random-secret"
  python app.py
  ```

## Notes

- User accounts are stored in `users.json` (created automatically, git-ignored).
- Market data is fetched server-side from CoinGecko and cached briefly to respect
  the free API's rate limits.
- The `/api/coins` endpoint requires an active login session.
