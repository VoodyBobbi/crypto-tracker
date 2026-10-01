"""Four step LONG averaging grid calculator."""

from __future__ import annotations

import math

MARGINS = (1.0, 2.0, 3.0, 4.0)
ENTRY_COEF = 0.85
LEVERAGE_MIN = 1.5
LEVERAGE_MAX = 500.0


class GridError(ValueError):
    """Invalid inputs or an unreachable liquidation target."""


def decimals_of(value: object) -> int:
    text = str(value).strip().replace(",", ".")
    return max(4, len(text.split(".", 1)[1]) if "." in text else 0)


def build_grid(p1: float, leverage: float, mmr: float = 0.0,
               funding: float = 0.0) -> list[dict]:
    if p1 <= 0:
        raise GridError("Цена входа должна быть больше нуля.")
    if leverage <= 0:
        raise GridError("Плечо должно быть больше нуля.")
    factor = 1 - 1 / leverage + mmr + funding
    rows: list[dict] = []
    total_margin = total_coins = 0.0
    price = p1
    for step, margin in enumerate(MARGINS, 1):
        if step > 1:
            previous = rows[-1]
            price = previous["avg"] - ENTRY_COEF * (previous["avg"] - previous["liq"])
        coins = margin * leverage / price
        total_margin += margin
        total_coins += coins
        average = leverage * total_margin / total_coins
        liquidation = average * factor
        rows.append({"step": step, "price": price, "margin": margin,
                     "coins": coins, "avg": average, "liq": liquidation})
    span = rows[0]["price"] - rows[-1]["price"]
    for row in rows:
        row["pct"] = ((rows[0]["price"] - row["price"]) / span * 100) if span else 0.0
    return rows


def solve_leverage(p1: float, target: float, mmr: float = 0.0,
                   funding: float = 0.0) -> float:
    rate = mmr + funding
    high = LEVERAGE_MAX if rate <= 0 else min(LEVERAGE_MAX, 1 / rate - 1e-9)
    if high <= LEVERAGE_MIN:
        raise GridError("Цель недостижима")
    def difference(value: float) -> float:
        return build_grid(p1, value, mmr, funding)[-1]["liq"] - target
    if difference(LEVERAGE_MIN) > 0 or difference(high) < 0:
        raise GridError("Цель недостижима")
    low = LEVERAGE_MIN
    for _ in range(200):
        mid = (low + high) / 2
        if difference(mid) < 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def calculate(p1_text: str, *, leverage: int | None = None,
              target: float | None = None, mmr_pct: float | None = None,
              liq1: float | None = None, funding_pct: float = 0.0,
              funding_count: int = 0) -> dict:
    p1_text = str(p1_text).strip().replace(",", ".")
    try:
        p1 = float(p1_text)
    except ValueError as exc:
        raise GridError("Введите цену входа.") from exc
    if not math.isfinite(p1) or p1 <= 0:
        raise GridError("Цена входа должна быть больше нуля.")
    funding = funding_pct * funding_count / 100
    if liq1 is not None:
        if leverage is None:
            raise GridError("ликв1 работает только вместе с плечом")
        mmr = liq1 / p1 - (1 - 1 / leverage)
        source = "калибровка"
    elif mmr_pct is not None:
        mmr, source = mmr_pct / 100, "задан"
    else:
        mmr, source = 0.0, "не задан"

    if leverage is None:
        if target is None or not 0 < target < p1:
            raise GridError("цель ликвидации должна быть ниже цены входа")
        exact = solve_leverage(p1, target, mmr, funding)
        leverage = max(2, min(math.floor(exact + 0.5), int(LEVERAGE_MAX)))
    if leverage * (mmr + funding) >= 1:
        raise GridError("при таком плече и MMR ликвидация выше цены входа")
    rows = build_grid(p1, leverage, mmr, funding)
    if any(b["price"] <= a["liq"] for a, b in zip(rows, rows[1:])):
        raise GridError("Вход следующего шага оказался ниже ликвидации.")
    return {"leverage": leverage, "places": decimals_of(p1_text),
            "mmr": mmr, "mmr_source": source,
            "funding_pct": funding_pct, "funding_count": funding_count,
            "rows": rows}
