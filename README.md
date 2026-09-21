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

---

# Калькулятор сеточной стратегии (LONG)

Вторая часть проекта: расчёт сетки усреднения с выбором пары из списка
бессрочных фьючерсов MEXC. Открывается по адресу `/calc`, регистрация не нужна.

## Как это устроено

Два слоя, как в техническом задании.

**Ядро (`grid.py`)** — чистая математика, ничего не требует. На вход два числа,
на выход четыре шага сетки: цена входа, маржа, плечо, ликвидация, % пути.
Жёсткие константы: 4 шага, маржа 1/2/3/4 USDT (сумма 10), коэффициент входа
0.85. Зависимостей нет — если scipy не установлена, подбор плеча идёт
встроенной бисекцией (функция монотонна, результат совпадает).

**Слой метаданных (`mexc.py`)** — опциональный, уточняет расчёт данными с биржи.
Читает публичные query-эндпоинты фьючерсного API MEXC (`contract.mexc.com`,
ключи не нужны), кэширует справочник контрактов на сутки в `.cache/`.
При недоступности биржи отдаёт просроченный кэш, а калькулятор продолжает
работать в ручном режиме.

| Что берём с биржи | Что уточняет |
|---|---|
| Тиры maintenance margin | k — главное улучшение точности |
| Максимальное плечо | Отсечение недостижимых целей до расчёта |
| Комиссия taker/maker | Реальная маржа шага вместо номинальной |
| Шаг цены и priceScale | Округление до значений, принимаемых биржей |
| Ставка фандинга | Справочно: списывается из маржи каждые 8 часов |
| Минимальный объём | Проверка, что шаг 1 вообще проходит |

## Два режима расчёта

- **Цель ликвидации → подобрать плечо.** Даны цена входа шага 1 и целевая
  ликвидация шага 4. Плечо подбирается численно и округляется до целого;
  итоговая ликвидация отклоняется от цели на величину округления — это
  ожидаемо и не компенсируется.
- **Плечо задано.** Даны цена входа и плечо. Ликвидация получается как
  результат.

## Три источника k

k — доля маржи, которая расходуется до ликвидации.

1. **k = 1.0** — идеализация: ликвидация ровно при исчерпании всей маржи.
2. **Тиры MMR с биржи** — `k = 1 − MMR × плечо`, где MMR берётся из тира под
   текущий размер позиции. Позиция растёт от шага к шагу, поэтому k
   пересчитывается на каждом шаге, а не берётся константой.
3. **Свой k** — калибровка по факту: `k = плечо × (1 − реальная ликвидация ÷ цена входа)`.
   Реальную ликвидацию видно в интерфейсе биржи после первого шага.

## Проверка ядра

```bash
python test_grid.py
```

Прогоняет оба контрольных примера из ТЗ (P1=100/L=10 и P1=1.4307/Ltarget=1.26),
инварианты (`pct[1] = 0`, `pct[4] = 100`, сумма маржи = 10, цены строго убывают)
и режим k из тиров MMR.

## Сверка имён полей MEXC

Документация MEXC перестраивалась, поэтому имена полей читаются через список
синонимов. Посмотреть сырой ответ по одному контракту:

```bash
python mexc.py CAKE_USDT
```

Выведет исходный JSON и результат нормализации рядом. То же через HTTP:
`GET /api/mexc/raw/CAKE_USDT`.

## Новые маршруты

| Маршрут | Назначение |
|---|---|
| `GET /calc` | Страница калькулятора |
| `GET /api/mexc/search?q=` | Поиск контрактов для панели выбора пары |
| `GET /api/mexc/contract/<symbol>` | Метаданные, цена и фандинг |
| `GET /api/mexc/raw/<symbol>` | Сырой JSON контракта для отладки |
| `POST /api/grid` | Расчёт сетки |

## Оговорки

- Разрядность вывода берёт `priceScale` контракта; без выбранной пары —
  разрядность введённой цены, но не меньше 4 знаков.
- Комиссия и фандинг показаны справочно и в цепочку цен не заложены.
- Часть эндпоинтов размещения ордеров у MEXC закрыта. Калькулятору нужны
  только query-эндпоинты, так что это не мешает.
- Это калькулятор, а не торговый советник.
