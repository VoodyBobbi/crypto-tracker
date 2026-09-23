"""Ядро расчёта сеточной стратегии (LONG, изолированная маржа).

Часть 1 технического задания. Чистая математика: никаких сетевых запросов,
никаких зависимостей кроме стандартной библиотеки (scipy используется, если
установлена, иначе работает встроенная бисекция).

Публичный интерфейс:
    build_grid(p1, leverage, k=1.0, mmr_fn=None) -> list[dict]
    solve_leverage(p1, target_liq, k=1.0, mmr_fn=None) -> (exact, rounded)
    decimals_of(value) -> int
"""

from __future__ import annotations

import math
from typing import Callable, Optional

# --------------------------------------------------------------------------- #
# Константы (п. 1.2 ТЗ)
# --------------------------------------------------------------------------- #
MARGINS = (1.0, 2.0, 3.0, 4.0)   # маржа по шагам, сумма = 10 USDT
ENTRY_COEF = 0.85                # вход на 85% пути от средней до ликвидации
STEPS = len(MARGINS)

LEVERAGE_MIN = 1.5               # нижняя граница интервала поиска плеча
LEVERAGE_MAX = 500.0             # верхняя граница

# mmr_fn(cum_coins) -> maintenance margin rate для текущего размера позиции
MmrFn = Callable[[float], float]


class GridError(ValueError):
    """Некорректные входные данные или недостижимая цель."""


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #
def decimals_of(value) -> int:
    """Количество знаков после запятой в том виде, в каком число введено.

    '1.4307' -> 4, '100' -> 0, 1.25 -> 2. Экспоненциальная запись не
    поддерживается намеренно: цены в неё не пишут.
    """
    text = str(value).strip()
    if "e" in text.lower():
        return 8
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1].rstrip())


def _k_for_step(leverage: float, k_const: float, mmr_fn: Optional[MmrFn],
                cum_coins: float) -> float:
    """Доля маржи до ликвидации на конкретном шаге.

    Без данных биржи k постоянна. С тирами MMR k = 1 - MMR * L и
    пересчитывается на каждом шаге: позиция растёт и может перескочить
    в следующий тир.
    """
    if mmr_fn is None:
        return k_const

    mmr = mmr_fn(cum_coins)
    if mmr is None:
        raise GridError("Нет ставки поддерживающей маржи — расчёт невозможен.")

    k = 1.0 - float(mmr) * leverage
    if k <= 0:
        # MMR * L >= 1: ликвидация оказывается выше средней цены, позиции
        # с таким плечом не существует. Клампить это нельзя — получится
        # нарисованная таблица вместо ошибки.
        raise GridError(
            f"Плечо {leverage:g}x невозможно при MMR {float(mmr) * 100:.3f}%: "
            f"предел для этой монеты — {1 / float(mmr):.0f}x."
        )
    return min(1.0, k)


# --------------------------------------------------------------------------- #
# Построение таблицы (п. 1.4 ТЗ)
# --------------------------------------------------------------------------- #
def build_grid(p1: float, leverage: float, k: float = 1.0,
               mmr_fn: Optional[MmrFn] = None,
               lot: Optional[float] = None, fee: float = 0.0) -> list[dict]:
    """Строит все 4 шага сетки для цены входа p1 и плеча leverage.

    lot — размер одного контракта в монетах. Если он задан, шаг считается так,
    как его исполнит биржа: из внесённой суммы вычитается комиссия за открытие,
    остаток делится на стоимость одного контракта, дробь отбрасывается. Фактическая
    маржа шага получается меньше запланированной, и это смещает среднюю цену,
    а за ней и все последующие точки входа.

    lot = None — идеализация дробными монетами (режим контрольных примеров ТЗ).
    """
    p1 = float(p1)
    leverage = float(leverage)
    if p1 <= 0:
        raise GridError("Цена входа должна быть больше нуля.")
    if leverage <= 1:
        raise GridError("Плечо должно быть больше 1.")
    if not 0 < k <= 1:
        raise GridError("k должно лежать в диапазоне (0, 1].")

    rows: list[dict] = []
    cum_margin = 0.0
    cum_coins = 0.0
    price = p1

    for i, margin in enumerate(MARGINS):
        if i > 0:
            prev = rows[-1]
            # Вход на 85% пути от средней цены до ликвидации предыдущего шага.
            price = prev["avg"] - ENTRY_COEF * (prev["avg"] - prev["liq"])
            if price <= 0:
                raise GridError(
                    "Цепочка ушла в отрицательные цены — плечо слишком велико."
                )

        if lot:
            # Стоимость одного контракта для покупателя = маржа под него + комиссия.
            per_contract = price * lot * (1 / leverage + fee)
            count = math.floor(margin / per_contract) if per_contract > 0 else 0
            if count < 1:
                raise GridError(
                    f"Шаг {i + 1}: на {margin:g} USDT при плече {leverage:g}x "
                    "не набирается даже один контракт."
                )
            coins = count * lot
            margin_used = coins * price / leverage
        else:
            # Множитель leverage обязателен: margin / price без плеча ломает цепочку.
            count = None
            coins = margin * leverage / price
            margin_used = margin

        cum_margin += margin_used
        cum_coins += coins
        avg = leverage * cum_margin / cum_coins
        step_k = _k_for_step(leverage, k, mmr_fn, cum_coins)
        liq = avg * (1 - step_k / leverage)

        rows.append({
            "step": i + 1,
            "price": price,
            "margin": margin,                      # запланировано: 1, 2, 3, 4 USDT
            "margin_used": margin_used,            # что реально уйдёт в маржу
            "fee": coins * price * fee,
            "contracts": count,
            "leverage": leverage,
            "coins": coins,
            "cum_coins": cum_coins,
            "cum_margin": cum_margin,
            "position_value": leverage * cum_margin,   # = avg * cum_coins
            "avg": avg,
            "liq": liq,
            "k": step_k,
        })

    # Колонка «% пути»: знаменатель — цена входа шага 4, не ликвидация.
    p_first, p_last = rows[0]["price"], rows[-1]["price"]
    span = p_first - p_last
    for row in rows:
        row["pct_path"] = (p_first - row["price"]) / span * 100 if span else 0.0

    return rows


# --------------------------------------------------------------------------- #
# Подбор плеча (Режим A, п. 1.5 ТЗ)
# --------------------------------------------------------------------------- #
def _residual(p1: float, target_liq: float, k: float,
              mmr_fn: Optional[MmrFn]) -> Callable[[float], float]:
    def f(leverage: float) -> float:
        return build_grid(p1, leverage, k, mmr_fn)[-1]["liq"] - target_liq
    return f


def _bisect(f: Callable[[float], float], lo: float, hi: float,
            xtol: float = 1e-10, max_iter: int = 200) -> float:
    """Бисекция на случай, когда scipy не установлена. Функция монотонна."""
    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        raise GridError("Корень не найден в интервале плеча.")
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        f_mid = f(mid)
        if f_mid == 0 or (hi - lo) / 2 < xtol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def _max_feasible(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Наибольшее плечо в [lo, hi], на котором сетка вообще строится."""
    try:
        f(hi)
        return hi
    except GridError:
        pass
    try:
        f(lo)
    except GridError as exc:
        raise GridError(f"Сетку не построить даже при плече {lo:g}x. {exc}") from exc

    good, bad = lo, hi
    for _ in range(60):
        mid = (good + bad) / 2
        try:
            f(mid)
            good = mid
        except GridError:
            bad = mid
    return good


def solve_leverage_exchange(p1: float, target_liq: float, k: float = 1.0,
                            mmr_fn: Optional[MmrFn] = None,
                            max_leverage: Optional[float] = None,
                            lot: float = 1.0, fee: float = 0.0) -> tuple[Optional[float], int]:
    """Подбор плеча для режима с целыми контрактами.

    Отбрасывание дроби делает liq[4] ступенчатой функцией плеча, поэтому
    бисекция здесь неприменима: перебираем целые плечи и берём ближайшее
    к цели, при равенстве — меньшее, оно безопаснее.
    """
    hi = int(min(float(max_leverage or LEVERAGE_MAX), LEVERAGE_MAX))
    best, best_gap = None, None

    for leverage in range(2, hi + 1):
        try:
            liq = build_grid(p1, leverage, k, mmr_fn, lot=lot, fee=fee)[-1]["liq"]
        except GridError:
            continue
        gap = abs(liq - target_liq)
        if best_gap is None or gap < best_gap - 1e-12:
            best, best_gap = leverage, gap

    if best is None:
        raise GridError(
            "Сетку не построить ни на одном плече: суммы шагов слишком малы "
            "для минимального контракта этой пары."
        )
    return None, best


def solve_leverage(p1: float, target_liq: float, k: float = 1.0,
                   mmr_fn: Optional[MmrFn] = None,
                   max_leverage: Optional[float] = None) -> tuple[float, int]:
    """Подбирает плечо так, чтобы ликвидация шага 4 попала в target_liq.

    Возвращает (точное плечо, округлённое до целого). Итоговая ликвидация
    после округления отклонится от цели — это ожидаемо и не компенсируется.
    """
    p1 = float(p1)
    target_liq = float(target_liq)
    if target_liq <= 0:
        raise GridError("Целевая ликвидация должна быть больше нуля.")
    if target_liq >= p1:
        raise GridError("Целевая ликвидация должна быть ниже цены входа (LONG).")

    hi = float(max_leverage) if max_leverage else LEVERAGE_MAX
    hi = min(hi, LEVERAGE_MAX)
    if hi <= LEVERAGE_MIN:
        raise GridError("Максимальное плечо контракта слишком мало для расчёта.")

    f = _residual(p1, target_liq, k, mmr_fn)

    # MMR ограничивает плечо сверху жёстче, чем лимит контракта: при MMR * L >= 1
    # позиция невозможна. Ищем наибольшее плечо, на котором сетка ещё строится.
    hi = _max_feasible(f, LEVERAGE_MIN, hi)

    # Проверяем достижимость до решения, чтобы дать внятную ошибку.
    f_lo, f_hi = f(LEVERAGE_MIN), f(hi)
    if f_lo * f_hi > 0:
        # liq[4] растёт вместе с плечом, поэтому границы интервала задают
        # весь достижимый диапазон ликвидации.
        liq_lo, liq_hi = f_lo + target_liq, f_hi + target_liq
        if f_hi < 0:
            raise GridError(
                f"Цель недостижима: при максимальном плече {hi:g}x ликвидация "
                f"шага 4 равна {liq_hi:.6g}, это ниже цели {target_liq:g}. "
                "Опустите цель или выберите контракт с бо́льшим плечом."
            )
        raise GridError(
            f"Цель недостижима: даже при плече {LEVERAGE_MIN:g}x ликвидация "
            f"шага 4 равна {liq_lo:.6g}, это выше цели {target_liq:g}. "
            "Поднимите цель ближе к цене входа."
        )

    try:
        from scipy.optimize import brentq  # type: ignore
        exact = float(brentq(f, LEVERAGE_MIN, hi, xtol=1e-10))
    except ImportError:
        exact = _bisect(f, LEVERAGE_MIN, hi)

    # Обычное округление, не банковское: round(18.5) в Python даёт 18.
    rounded = int(math.floor(exact + 0.5))
    rounded = max(2, min(rounded, int(hi)))
    return exact, rounded
