"""Регрессионные тесты ядра — контрольные примеры из п. 1.7 ТЗ.

Запуск: python test_grid.py
"""

from grid import build_grid, solve_leverage, decimals_of

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


if __name__ == "__main__":
    results = [
        test_mode_b(),
        test_mode_a(),
        test_invariants(),
        test_decimals(),
        test_mmr_mode(),
    ]
    print()
    print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ" if all(results) else "ЕСТЬ ПРОВАЛЕННЫЕ ТЕСТЫ")
    raise SystemExit(0 if all(results) else 1)
