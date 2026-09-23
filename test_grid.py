"""Регрессионные тесты ядра — контрольные примеры из п. 1.7 ТЗ.

Запуск: python test_grid.py
"""

import math

import mexc
from grid import GridError, build_grid, solve_leverage, decimals_of

TOL = 5e-4


def _check(name, got, expected, tol=TOL):
    ok = abs(got - expected) <= tol
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {name}: получено {got:.4f}, ожидалось {expected:.4f}")
    return ok


def test_mode_b():
    """P1 = 100, L = 10, k = 1.0"""
    print("Тест 1 (Режим Б): P1=100, L=10, k=1.0")
    rows = build_grid(100, 10, 1.0)
    expected = [
        (100.0000, 90.0000, 0.00),
        (91.5000, 84.7513, 48.13),
        (86.1638, 80.9895, 78.34),
        (82.3393, 78.0879, 100.00),
    ]
    ok = True
    for row, (price, liq, pct) in zip(rows, expected):
        n = row["step"]
        ok &= _check(f"шаг {n} цена входа", row["price"], price)
        ok &= _check(f"шаг {n} ликвидация", row["liq"], liq)
        ok &= _check(f"шаг {n} % пути", row["pct_path"], pct, tol=0.01)
    return ok


def test_mode_a():
    """P1 = 1.4307, Ltarget = 1.26 -> L_exact ~ 18.9223, L_final = 19"""
    print("Тест 2 (Режим A): P1=1.4307, Ltarget=1.26")
    exact, final = solve_leverage(1.4307, 1.26)
    ok = _check("точное плечо", exact, 18.9223, tol=1e-3)
    ok &= _check("округлённое плечо", float(final), 19.0)
    rows = build_grid(1.4307, final)
    ok &= _check("итоговая ликвидация шага 4", rows[-1]["liq"], 1.2607, tol=1e-4)
    return ok


def test_invariants():
    """Инварианты, которые должны держаться на любых входных данных."""
    print("Тест 3: инварианты")
    rows = build_grid(1.4307, 19)
    ok = _check("pct[1]", rows[0]["pct_path"], 0.0, tol=1e-9)
    ok &= _check("pct[4]", rows[-1]["pct_path"], 100.0, tol=1e-9)
    ok &= _check("сумма маржи", rows[-1]["cum_margin"], 10.0)
    prices = [r["price"] for r in rows]
    strictly_down = all(a > b for a, b in zip(prices, prices[1:]))
    print(f"  [{'ok  ' if strictly_down else 'FAIL'}] цены входа строго убывают")
    ok &= strictly_down
    return ok


def test_decimals():
    print("Тест 4: разрядность из входного P1")
    cases = [("1.4307", 4), ("100", 0), ("0.00012345", 8), ("2.5", 1)]
    ok = True
    for text, expected in cases:
        got = decimals_of(text)
        good = got == expected
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] {text!r} -> {got} (ждём {expected})")
    return ok


def test_mmr_mode():
    """k из MMR: k = 1 - MMR * L. При MMR=0 поведение совпадает с k=1."""
    print("Тест 5: k из тиров MMR")
    base = build_grid(100, 10, k=1.0)
    zero_mmr = build_grid(100, 10, mmr_fn=lambda coins: 0.0)
    ok = _check("liq[4] при MMR=0", zero_mmr[-1]["liq"], base[-1]["liq"], tol=1e-9)
    tiered = build_grid(100, 10, mmr_fn=lambda coins: 0.004)
    ok &= _check("k при MMR=0.4%, L=10", tiered[0]["k"], 0.96, tol=1e-9)
    higher = tiered[-1]["liq"] > base[-1]["liq"]
    print(f"  [{'ok  ' if higher else 'FAIL'}] ликвидация с MMR выше идеальной")
    return ok & higher


def _ok(name, condition):
    print(f"  [{'ok  ' if condition else 'FAIL'}] {name}")
    return condition


def test_no_silent_zero_mmr():
    """MMR отсутствует -> None, а не нуль. Молчаливый идеал запрещён."""
    print("Тест 6: отсутствие MMR не превращается в нуль")
    bare = mexc.normalize({"symbol": "X_USDT", "maxLeverage": 50})
    ok = _ok("mmr_base = None", bare["mmr_base"] is None)
    ok &= _ok("has_mmr = False", mexc.has_mmr(bare) is False)
    ok &= _ok("mmr_for_volume = None", mexc.mmr_for_volume(bare, 100) is None)
    ok &= _ok("make_mmr_fn = None", mexc.make_mmr_fn(bare) is None)
    return ok


def test_impossible_leverage():
    """MMR * L >= 1 -> ошибка, а не нарисованная таблица."""
    print("Тест 7: невозможное плечо не клампится")
    try:
        build_grid(100, 60, mmr_fn=lambda coins: 0.02)   # 0.02 * 60 = 1.2
    except GridError:
        return _ok("ошибка вместо k = 0.01", True)
    return _ok("ошибка вместо k = 0.01", False)


def test_leverage_bound():
    """Подбор плеча сужает верхнюю границу до допустимой при данном MMR."""
    print("Тест 8: граница поиска плеча учитывает MMR")
    mmr = 0.01                       # предел 100x
    exact, final = solve_leverage(100, 95, mmr_fn=lambda coins: mmr,
                                  max_leverage=400)
    ok = _ok(f"плечо {final}x ниже предела 100x", final < 1 / mmr)
    rows = build_grid(100, final, mmr_fn=lambda coins: mmr)
    ok &= _ok("сетка строится", len(rows) == 4)
    return ok


def test_rounding_and_value():
    """Обычное округление, не банковское. Объём — по средней цене."""
    print("Тест 9: округление плеча и объём позиции")
    ok = _ok("floor(18.5 + 0.5) = 19", int(math.floor(18.5 + 0.5)) == 19)
    rows = build_grid(100, 10)
    last = rows[-1]
    ok &= _ok("объём = плечо x маржа", abs(last["position_value"] - 100.0) < 1e-9)
    ok &= _ok("объём = средняя x монеты",
              abs(last["position_value"] - last["avg"] * last["cum_coins"]) < 1e-9)
    return ok


def test_symbols():
    print("Тест 10: символы приводятся к виду BASE_USDT")
    cases = {"cake": "CAKE_USDT", "CAKEUSDT": "CAKE_USDT", "CAKE/USDT": "CAKE_USDT",
             " cake_usdt ": "CAKE_USDT", "BTC": "BTC_USDT"}
    ok = True
    for raw, expected in cases.items():
        got = mexc.normalize_symbol(raw)
        ok &= _ok(f"{raw!r} -> {got}", got == expected)
    return ok


def test_entries_above_liquidation():
    """Ключевой инвариант: каждый вход выше ликвидации предыдущего шага."""
    print("Тест 11: вход n+1 выше ликвидации n")
    ok = True
    for leverage, mmr in ((10, 0.0), (61, 0.004), (76, 0.002), (113, 0.001)):
        rows = build_grid(100, leverage, mmr_fn=lambda coins, m=mmr: m)
        good = all(rows[i + 1]["price"] > rows[i]["liq"] for i in range(3))
        ok &= _ok(f"{leverage}x при MMR {mmr * 100:.1f}%", good)
    return ok


def test_exchange_lots():
    """Целые контракты и комиссия: сверка с реальным ордером на бирже.

    Скрин формы ордера: цена 1.7454, плечо 61x, внесён 1 USDT, тейкер 0.06% —
    биржа набрала 33 контракта (дробное 33.71 отброшено вниз).
    """
    print("Тест 12: шаг считается целыми контрактами")
    rows = build_grid(1.7454, 61, mmr_fn=lambda c: 0.004, lot=1.0, fee=0.0006)
    ok = _ok("шаг 1 = 33 контракта", rows[0]["contracts"] == 33)
    ok &= _ok("фактическая маржа меньше внесённой",
              rows[0]["margin_used"] < rows[0]["margin"])
    ok &= _ok("маржа + комиссия не превышают внесённое",
              all(r["margin_used"] + r["fee"] <= r["margin"] + 1e-9 for r in rows))
    ok &= _ok("количество кратно размеру контракта",
              all(abs(r["coins"] / 1.0 - round(r["coins"] / 1.0)) < 1e-9 for r in rows))
    ok &= _ok("средняя = объём / количество",
              all(abs(r["avg"] * r["cum_coins"] - r["position_value"]) < 1e-6 for r in rows))
    ok &= _ok("вход n+1 выше ликвидации n",
              all(rows[i + 1]["price"] > rows[i]["liq"] for i in range(3)))
    return ok


def test_lots_do_not_break_ideal():
    """Без размера контракта поведение прежнее — контрольные примеры ТЗ."""
    print("Тест 13: режим без лотов не изменился")
    rows = build_grid(100, 10)
    ok = _ok("liq[4] = 78.0879", abs(rows[-1]["liq"] - 78.0879) < 5e-4)
    ok &= _ok("contracts = None", rows[0]["contracts"] is None)
    ok &= _ok("маржа не урезана", rows[0]["margin_used"] == rows[0]["margin"])
    return ok


if __name__ == "__main__":
    results = [
        test_mode_b(),
        test_mode_a(),
        test_invariants(),
        test_decimals(),
        test_mmr_mode(),
        test_no_silent_zero_mmr(),
        test_impossible_leverage(),
        test_leverage_bound(),
        test_rounding_and_value(),
        test_symbols(),
        test_entries_above_liquidation(),
        test_exchange_lots(),
        test_lots_do_not_break_ideal(),
    ]
    print()
    print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ" if all(results) else "ЕСТЬ ПРОВАЛЕННЫЕ ТЕСТЫ")
    raise SystemExit(0 if all(results) else 1)
