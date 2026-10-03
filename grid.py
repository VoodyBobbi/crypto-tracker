"""MEXC-aware four-step LONG grid risk estimate."""

from __future__ import annotations

import math
import time

MARGINS = (1.0, 2.0, 3.0, 4.0)
ENTRY_COEF = 0.85
GRID_BUDGET = 10.0
DEFAULT_HOURS_UNTIL_STEP4 = 24.0
API_MAKER_FEE_FLOOR = 0.0006
API_TAKER_FEE_FLOOR = 0.0008
MARGIN_SCALE_STEP = 0.002
FUNDING_SAFETY = 3.0
FUNDING_FLOOR = 0.0001


class GridError(ValueError):
    """Invalid input or missing exchange data needed for a risk estimate."""


def _num(value, name: str, default=None) -> float:
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    if default is not None:
        return float(default)
    raise GridError(f"В ответе MEXC отсутствует корректное поле {name}.")


def _int(value, name: str, default=None) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        if default is not None:
            return int(default)
        raise GridError(f"В ответе MEXC отсутствует корректное поле {name}.")


def _active(position: dict) -> bool:
    return _int(position.get("state"), "position.state", 1) == 1


def _risk_metrics(contract: dict, volume: float, position_value: float) -> list[float]:
    risk_type = str(contract.get("riskLimitType") or "BY_VOLUME").upper()
    if risk_type == "BY_VALUE":
        # MEXC uses maxVol for tier boundaries; test both units and keep the stricter result.
        return [volume, position_value]
    if risk_type in {"", "BY_VOLUME"}:
        return [volume]
    raise GridError(f"Неизвестный тип риск-лимита MEXC: {risk_type}.")


def _mmr(contract: dict, volume: float, position_value: float) -> float:
    """Return the maintenance margin rate for the projected position size."""
    metrics = _risk_metrics(contract, volume, position_value)
    mode = str(contract.get("riskLimitMode") or "").upper()
    custom = contract.get("riskLimitCustom") or []
    if mode == "CUSTOM":
        if not custom:
            raise GridError("MEXC не вернула таблицу уровней риска для контракта.")
        tiers = sorted(custom, key=lambda tier: _num(tier.get("maxVol"), "riskLimitCustom.maxVol"))
        rates = []
        for metric in metrics:
            tier = next((tier for tier in tiers
                         if metric <= _num(tier.get("maxVol"), "riskLimitCustom.maxVol")), None)
            if tier is None:
                raise GridError("Сетка превышает максимальный уровень риска MEXC для этой пары.")
            rates.append(_num(tier.get("mmr"), "riskLimitCustom.mmr"))
        return max(rates)

    if mode != "INCREASE":
        raise GridError(f"Неизвестный режим уровней риска MEXC: {mode or 'пусто'}.")
    rate = _num(contract.get("maintenanceMarginRate"), "maintenanceMarginRate")
    base_limit = _num(contract.get("riskBaseVol"), "riskBaseVol")
    increment = _num(contract.get("riskIncrVol"), "riskIncrVol")
    increment_mmr = _num(contract.get("riskIncrMmr"), "riskIncrMmr")
    level_limit = _int(contract.get("riskLevelLimit"), "riskLevelLimit")
    rates = []
    for metric in metrics:
        tier_rate = rate
        if increment > 0 and metric > base_limit:
            extra_levels = math.ceil((metric - base_limit) / increment)
            if level_limit and extra_levels >= level_limit:
                raise GridError("Сетка превышает максимальный уровень риска MEXC для этой пары.")
            tier_rate += extra_levels * increment_mmr
        rates.append(tier_rate)
    return max(rates)


def _tier_max_leverage(contract: dict, volume: float, position_value: float) -> int:
    metrics = _risk_metrics(contract, volume, position_value)
    mode = str(contract.get("riskLimitMode") or "").upper()
    custom = contract.get("riskLimitCustom") or []
    if mode == "CUSTOM":
        if not custom:
            raise GridError("MEXC не вернула таблицу уровней риска для контракта.")
        tiers = sorted(custom, key=lambda tier: _num(tier.get("maxVol"), "riskLimitCustom.maxVol"))
        limits = []
        for metric in metrics:
            tier = next((tier for tier in tiers
                         if metric <= _num(tier.get("maxVol"), "riskLimitCustom.maxVol")), None)
            if tier is None:
                raise GridError("Сетка превышает максимальный уровень риска MEXC для этой пары.")
            limits.append(_int(tier.get("maxLeverage"), "riskLimitCustom.maxLeverage"))
        return min(limits)

    if mode != "INCREASE":
        raise GridError(f"Неизвестный режим уровней риска MEXC: {mode or 'пусто'}.")
    maximum = _int(contract.get("max_leverage"), "maxLeverage")
    base_limit = _num(contract.get("riskBaseVol"), "riskBaseVol")
    increment = _num(contract.get("riskIncrVol"), "riskIncrVol")
    for metric in metrics:
        if increment > 0 and metric > base_limit:
            levels = math.ceil((metric - base_limit) / increment)
            imr = (_num(contract.get("initialMarginRate"), "initialMarginRate")
                   + levels * _num(contract.get("riskIncrImr"), "riskIncrImr"))
            if imr > 0:
                maximum = min(maximum, math.floor(1 / imr))
    return maximum


def _check_pending_orders(context: dict) -> None:
    def increases_risk(order: dict) -> bool:
        state = _int(order.get("state"), "order.state", 1)
        side = _int(order.get("side"), "order.side", 0)
        reduce_only = str(order.get("reduceOnly", "")).lower() in {"true", "1"}
        if state not in (0, 1, 2) or side not in (1, 3) or reduce_only:
            return False
        return True

    for key in ("orders", "plan_orders", "trailing_orders"):
        if any(increases_risk(order) for order in context.get(key, [])):
            raise GridError(
                "Есть активная заявка на открытие, которая может изменить риск до шага 4. "
                "Отмените её или дождитесь исполнения, затем обновите расчёт."
            )

    for order in context.get("stop_orders", []):
        if (_int(order.get("state"), "stop_order.state", 1) == 1
                and (_int(order.get("takeProfitReverse"), "takeProfitReverse", 2) == 1
                     or _int(order.get("stopLossReverse"), "stopLossReverse", 2) == 1)):
            raise GridError(
                "Есть активный TP/SL-ордер с разворотом позиции; "
                "его влияние нельзя безопасно оценить."
            )


def _check_market_freshness(context: dict, funding: dict) -> None:
    market = context.get("market") or {}
    now_ms = int(time.time() * 1000)
    for name, data in (("fair price", market), ("funding", funding)):
        _check_timestamp(data.get("timestamp"), f"{name}.timestamp", now_ms)


def _check_timestamp(value, name: str, now_ms: int | None = None) -> None:
    timestamp = _num(value, name)
    age_ms = (now_ms or int(time.time() * 1000)) - timestamp
    if age_ms > 30_000 or age_ms < -10_000:
        raise GridError("Данные MEXC устарели или имеют неверное время; обновите страницу.")


def _fee_rate(context: dict) -> float:
    keys = {
        "makerFeeRate", "takerFeeRate", "realMakerFee", "realTakerFee",
        "originalMakerFee", "originalTakerFee", "makerFee", "takerFee",
    }
    found = []

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys:
                    try:
                        rate = float(item)
                        if math.isfinite(rate) and rate >= 0:
                            found.append(rate)
                    except (TypeError, ValueError):
                        pass
                elif isinstance(item, (dict, list)):
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(context.get("fee") or {})
    collect(context.get("contract") or {})
    if not found:
        fallback = context["contract"].get("takerFeeRate")
        if fallback is not None:
            found.append(_num(fallback, "takerFeeRate"))
    # The highest current maker/taker/tier rate is used as a conservative estimate.
    # MEXC's published Futures API taker rate is 0.08%; account-specific
    # exchange data can raise this conservative floor.
    return max(max(found), API_TAKER_FEE_FLOOR)


def _funding_events(funding: dict, hours: float) -> int:
    if hours <= 0:
        return 0
    cycle = _int(funding.get("collectCycle"), "funding.collectCycle")
    if cycle <= 0:
        raise GridError("MEXC вернула некорректный интервал фандинга.")
    next_time = _num(funding.get("nextSettleTime"), "funding.nextSettleTime")
    now_ms = int(time.time() * 1000)
    cycle_ms = cycle * 60 * 60 * 1000
    while next_time <= now_ms:
        next_time += cycle_ms
    end_time = now_ms + hours * 60 * 60 * 1000
    if next_time > end_time:
        return 0
    return 1 + int((end_time - next_time) // cycle_ms)


def funding_event_count(funding: dict, hours: float) -> int:
    """Count scheduled settlements through the horizon plus one reserve event."""
    if hours <= 0:
        return 0
    return _funding_events(funding, hours) + 1


def funding_stress_rate(funding: dict, position_type: int) -> float:
    """Estimate adverse funding per settlement with a local safety buffer.

    Funding is only known near the next settlement. The multiplier and floor
    are conservative planning assumptions; the API-provided cap is used as
    the current upper bound, not as a guarantee about future settlements.
    """
    current = _num(funding.get("fundingRate"), "funding.fundingRate")
    if position_type == 1:  # Longs pay when funding is positive.
        cap = max(0.0, _num(funding.get("maxFundingRate"), "funding.maxFundingRate"))
        paying_rate = max(0.0, current)
    else:  # Shorts pay when funding is negative.
        cap = max(0.0, -_num(funding.get("minFundingRate"), "funding.minFundingRate"))
        paying_rate = max(0.0, -current)
    return min(cap, max(FUNDING_FLOOR, paying_rate * FUNDING_SAFETY))


def _leverage_settings(context: dict, target_positions: list[dict]) -> tuple[int, int, int, int]:
    settings = context.get("leverage_settings") or []
    if isinstance(settings, dict):
        settings = settings.get("list") or settings.get("data") or []
    longs = [row for row in settings if _int(row.get("positionType"), "positionType", 1) == 1]
    if not longs:
        raise GridError("MEXC не вернула настройки плеча для LONG.")

    position_modes = {_int(row.get("openType"), "position.openType") for row in target_positions}
    if len(position_modes) > 1:
        raise GridError("У пары найдены позиции в разных режимах маржи; расчёт остановлен.")
    if position_modes:
        open_type = next(iter(position_modes))
        matching = [row for row in longs if _int(row.get("openType"), "openType") == open_type]
        if not matching:
            raise GridError("MEXC не вернула настройки плеча для режима открытой позиции.")
        configured = matching[0]
    else:
        isolated = [row for row in longs if _int(row.get("openType"), "openType") == 1]
        configured = (isolated or longs)[0]
        open_type = _int(configured.get("openType"), "openType")
    if open_type not in (1, 2):
        raise GridError("MEXC вернула неизвестный режим маржи.")

    contract = context["contract"]
    max_leverage = _num(contract.get("max_leverage"), "maxLeverage")
    account_max = _num(configured.get("maxLeverageView"), "maxLeverageView", max_leverage)
    fee_max = _num((context.get("fee") or {}).get("maxLeverage"), "fee.maxLeverage", max_leverage)
    max_leverage = int(min(max_leverage, account_max, fee_max))
    risk_limits = context.get("risk_limits") or {}
    if isinstance(risk_limits, dict):
        risk_limits = risk_limits.get(context["symbol"], [])
    if isinstance(risk_limits, dict):
        risk_limits = risk_limits.get("list") or [risk_limits]
    account_tier_limits = [
        _int(row.get("maxLeverage"), "risk.maxLeverage")
        for row in risk_limits or []
        if _int(row.get("positionType"), "risk.positionType", 1) == 1
        and _int(row.get("openType"), "risk.openType", open_type) == open_type
        and row.get("maxLeverage") is not None
    ]
    if account_tier_limits:
        max_leverage = min(max_leverage, *account_tier_limits)
    min_leverage = max(
        _int(contract.get("minLeverage"), "minLeverage", 1),
        _int((context.get("fee") or {}).get("minLeverage"), "fee.minLeverage", 1),
    )
    current_leverage = _int(configured.get("leverage"), "leverage")
    return open_type, current_leverage, min_leverage, max_leverage


def _position_values(position: dict, contract: dict) -> tuple[float, float, float]:
    volume = _num(position.get("holdVol"), "position.holdVol")
    contract_size = _num(contract.get("contractSize"), "contractSize")
    entry = _num(position.get("holdAvgPrice"), "position.holdAvgPrice")
    quantity = volume * contract_size
    return volume, quantity, entry * quantity


def _account_mmr(context: dict, position_type: int, open_type: int,
                 symbol: str | None = None) -> float:
    symbol = symbol or context["symbol"]
    risk_limits = (context.get("risk_limits_by_symbol") or {}).get(
        symbol, context.get("risk_limits")
    ) or {}
    if isinstance(risk_limits, dict):
        rows = risk_limits.get(symbol, [])
        if isinstance(rows, dict):
            rows = rows.get("list") or [rows]
    else:
        rows = risk_limits
    for row in rows or []:
        if (_int(row.get("positionType"), "risk.positionType", position_type) == position_type
                and _int(row.get("openType"), "risk.openType", open_type) == open_type):
            value = row.get("currentMmr", row.get("mmr"))
            if value is not None:
                return _num(value, "risk.currentMmr")
    settings = (context.get("leverage_settings_by_symbol") or {}).get(
        symbol, context.get("leverage_settings")
    ) or []
    if isinstance(settings, dict):
        settings = settings.get("list") or settings.get("data") or []
    for row in settings:
        if (_int(row.get("positionType"), "setting.positionType", position_type) == position_type
                and _int(row.get("openType"), "setting.openType", open_type) == open_type
                and row.get("currentMmr") is not None):
            return _num(row["currentMmr"], "setting.currentMmr")
    return 0.0


def _asset_usdt(context: dict) -> dict:
    assets = context.get("assets") or []
    usdt = next((row for row in assets if str(row.get("currency", "")).upper() == "USDT"), None)
    if not usdt:
        raise GridError("В MEXC Futures аккаунте не найден баланс USDT.")
    return usdt


def _calculate_for_leverage(context: dict, p1: float, leverage: int,
                            hours: float, opened_mode: int,
                            target_positions: list[dict],
                            margin_scale: float = 1.0) -> dict:
    contract = context["contract"]
    symbol = context["symbol"]
    fair_price = _num(context.get("fair_price"), "fairPrice")
    if fair_price <= 0:
        raise GridError("MEXC вернула некорректную fair price.")
    funding = context.get("funding") or {}
    _check_market_freshness(context, funding)
    funding_rate = funding_stress_rate(funding, 1)
    funding_events = funding_event_count(funding, hours)
    fee_rate = _fee_rate(context)
    liquidation_fee_rate = _num(contract.get("liquidationFeeRate"), "liquidationFeeRate")
    contract_size = _num(contract.get("contractSize"), "contractSize")
    volume_unit = _num(contract.get("volUnit"), "volUnit")
    min_volume = _num(contract.get("minVol"), "minVol")
    price_unit = _num(contract.get("priceUnit"), "priceUnit")
    contract_max_volume = _num(contract.get("maxVol"), "maxVol")
    per_order_limit = _num(contract.get("limitMaxVol"), "limitMaxVol", 0)
    max_order_volume = min(contract_max_volume, per_order_limit) if per_order_limit > 0 else contract_max_volume
    if (contract_size <= 0 or volume_unit <= 0 or min_volume <= 0
            or price_unit <= 0 or max_order_volume <= 0):
        raise GridError("В MEXC указаны некорректные размер контракта или шаг объёма.")

    active_positions = [row for row in context.get("positions", []) if _active(row)]
    other_assets = [
        row for row in context.get("assets", [])
        if str(row.get("currency", "")).upper() != "USDT"
        and _num(row.get("equity"), "asset.equity", 0) > 1e-8
    ]
    if opened_mode == 2 and other_assets:
        raise GridError(
            "Обнаружены активы кроме USDT. Для Multi-Asset режима нужна отдельная "
            "оценка залога; расчёт остановлен, чтобы не занизить риск."
        )

    target_longs = [row for row in target_positions
                    if _int(row.get("positionType"), "positionType") == 1]
    target_shorts = [row for row in target_positions
                     if _int(row.get("positionType"), "positionType") == 2]
    if len(target_longs) > 1 or len(target_shorts) > 1:
        raise GridError("Найдено несколько позиций одного направления по паре; проверьте аккаунт.")
    for row in target_positions:
        if _int(row.get("openType"), "openType") != opened_mode:
            raise GridError("По паре уже есть позиция в другом режиме маржи.")
        existing_leverage = _int(row.get("leverage"), "position.leverage")
        if existing_leverage != leverage:
            raise GridError("Плечо открытой позиции отличается от расчётного.")

    long_volume = long_quantity = long_entry_value = 0.0
    short_volume = short_quantity = short_entry_value = 0.0
    isolated_base_margin = 0.0
    current_isolated_liq = None
    for row in target_longs:
        volume, quantity, entry_value = _position_values(row, contract)
        long_volume += volume
        long_quantity += quantity
        long_entry_value += entry_value
        if opened_mode == 1:
            liq = _num(row.get("liquidatePrice"), "position.liquidatePrice")
            if liq <= 0:
                raise GridError("MEXC не вернула ликвидацию открытой изолированной позиции.")
            current_isolated_liq = liq
            risk_value = max(entry_value, fair_price * quantity)
            rate = max(_mmr(contract, volume, risk_value), _account_mmr(context, 1, 1))
            isolated_base_margin += entry_value * (1 + rate) - liq * quantity * (1 - liquidation_fee_rate)
    for row in target_shorts:
        volume, quantity, entry_value = _position_values(row, contract)
        short_volume += volume
        short_quantity += quantity
        short_entry_value += entry_value

    # Cross-margin equity includes the shared USDT wallet and the PnL of other cross positions.
    account = _asset_usdt(context)
    cross_buffer = 0.0
    other_cross_maintenance = 0.0
    other_cross_liquidation_fee = 0.0
    other_future_funding = 0.0
    if opened_mode == 2:
        frozen = _num(account.get("frozenBalance"), "account.frozenBalance", 0)
        equity = _num(account.get("equity"), "account.equity")
        account_unrealized = _num(account.get("unrealized"), "account.unrealized")
        bonus = _num(account.get("bonus"), "account.bonus", 0)
        wallet_balance = equity - account_unrealized - bonus
        isolated_margin = 0.0
        other_cross_pnl = 0.0
        for row in active_positions:
            mode = _int(row.get("openType"), "position.openType")
            other_symbol = str(row.get("symbol", ""))
            info = context.get("contracts", {}).get(other_symbol)
            if not info or str(info.get("settleCoin", "")).upper() != "USDT":
                raise GridError(
                    f"Нет поддерживаемых USDT-риск-параметров для открытой позиции {other_symbol}."
                )
            if mode == 1:
                position_margin = row.get("im")
                if position_margin is None:
                    position_margin = row.get("oim")
                isolated_margin += _num(position_margin, "position.im")
                continue
            if other_symbol == symbol:
                continue
            volume, quantity, entry_value = _position_values(row, info)
            other_market = context.get("funding_by_symbol", {}).get(other_symbol) or {}
            _check_timestamp(other_market.get("timestamp"), f"{other_symbol}.funding.timestamp")
            other_fair = _num(other_market.get("fairPrice"), f"{other_symbol}.fairPrice")
            risk_volume = volume
            risk_value = max(entry_value, other_fair * quantity)
            if _int(info.get("riskLongShortSwitch"), "riskLongShortSwitch", 0) == 0:
                for peer in active_positions:
                    if (str(peer.get("symbol", "")) == other_symbol
                            and _int(peer.get("openType"), "position.openType") == 2
                            and peer is not row):
                        peer_volume, peer_quantity, peer_entry = _position_values(peer, info)
                        risk_volume += peer_volume
                        risk_value += max(peer_entry, other_fair * peer_quantity)
            rate = _mmr(info, risk_volume, risk_value)
            rate = max(rate, _account_mmr(context, _int(row.get("positionType"), "position.positionType"),
                                         2, other_symbol))
            other_cross_maintenance += entry_value * rate
            other_cross_pnl += _num(row.get("unRealizedPnl"), f"{other_symbol}.unRealizedPnl")
            other_cross_liquidation_fee += _num(
                info.get("liquidationFeeRate"), f"{other_symbol}.liquidationFeeRate"
            ) * other_fair * quantity
            other_funding = context.get("funding_by_symbol", {}).get(other_symbol)
            if not other_funding:
                raise GridError(f"Нет ставки фандинга для открытой позиции {other_symbol}.")
            side = _int(row.get("positionType"), "position.positionType")
            adverse_rate = funding_stress_rate(other_funding, side)
            other_events = funding_event_count(other_funding, hours)
            market_fair = _num(other_funding.get("fairPrice"), f"{other_symbol}.fairPrice")
            other_notional = max(entry_value, market_fair * quantity)
            other_future_funding += adverse_rate * other_notional * other_events
        cross_buffer = wallet_balance - isolated_margin - frozen + other_cross_pnl - other_future_funding

    available_value = account.get("availableOpen")
    if available_value is None:
        available_value = account.get("availableBalance")
    cash_available = _num(available_value, "account.availableOpen")
    rows = []
    total_new_margin = total_entry_fees = 0.0
    total_new_volume = total_new_quantity = 0.0
    total_new_entry_value = 0.0
    price = p1

    for step, weight in enumerate(MARGINS, 1):
        margin = MARGINS[0] if step == 1 else weight * margin_scale
        if price_unit > 0:
            price = math.floor(price / price_unit + 1e-10) * price_unit
            if rows and price <= rows[-1]["liq"]:
                raise GridError("После округления по шагу цены следующий вход оказался у ликвидации.")
            if price <= 0:
                raise GridError("Цена входа меньше минимального шага цены MEXC.")
        raw_volume = margin * leverage / (price * contract_size)
        volume = math.floor(raw_volume / volume_unit) * volume_unit
        if volume < min_volume:
            raise GridError(
                f"Маржи шага {step} недостаточно для минимального объёма контракта MEXC."
            )
        if volume > max_order_volume:
            raise GridError(f"Объём шага {step} превышает лимит ордера MEXC.")
        quantity = volume * contract_size
        entry_value = price * quantity
        actual_margin = entry_value / leverage
        entry_fee = entry_value * fee_rate
        total_new_margin += actual_margin
        total_entry_fees += entry_fee
        total_new_volume += volume
        total_new_quantity += quantity
        total_new_entry_value += entry_value

        all_long_volume = long_volume + total_new_volume
        all_long_quantity = long_quantity + total_new_quantity
        all_long_entry = long_entry_value + total_new_entry_value
        long_risk_value = max(all_long_entry, fair_price * all_long_quantity)
        short_risk_value = max(short_entry_value, fair_price * short_quantity)
        all_short_rate = (max(_mmr(contract, short_volume, short_risk_value),
                              _account_mmr(context, 2, opened_mode))
                          if short_volume else 0.0)
        all_long_rate = max(_mmr(contract, all_long_volume, long_risk_value),
                            _account_mmr(context, 1, opened_mode))
        risk_limit_volume = all_long_volume
        risk_limit_value = long_risk_value
        if short_volume and _int(contract.get("riskLongShortSwitch"),
                                 "riskLongShortSwitch", 0) == 0:
            risk_limit_volume = all_long_volume + short_volume
            risk_limit_value = max(
                all_long_entry + short_entry_value,
                fair_price * (all_long_quantity + short_quantity),
            )
            shared_rate = max(
                _mmr(contract, risk_limit_volume, risk_limit_value),
                _account_mmr(context, 1, opened_mode),
                _account_mmr(context, 2, opened_mode),
            )
            all_long_rate = max(all_long_rate, shared_rate)
            all_short_rate = max(all_short_rate, shared_rate)
        if leverage > _tier_max_leverage(contract, risk_limit_volume, risk_limit_value):
            raise GridError("Плечо выше лимита MEXC для объёма этой ступени риска.")
        long_maintenance = all_long_entry * all_long_rate
        short_maintenance = short_entry_value * all_short_rate
        funding_cost = funding_rate * long_risk_value * funding_events
        if opened_mode == 2 and short_quantity:
            short_funding_rate = funding_stress_rate(funding, 2)
            funding_cost += short_funding_rate * short_risk_value * funding_events
        if opened_mode == 1:
            margin_buffer = isolated_base_margin + total_new_margin - total_entry_fees - funding_cost
            numerator = (all_long_entry + long_maintenance
                         - margin_buffer)
            denominator = all_long_quantity - liquidation_fee_rate * all_long_quantity
            if denominator <= 0:
                raise GridError("Не удалось рассчитать цену ликвидации по данным MEXC.")
            liquidation = numerator / denominator
        else:
            # Forecast funding and entry fees reduce shared cross-margin equity.
            target_net_quantity = all_long_quantity - short_quantity
            target_total_quantity = all_long_quantity + short_quantity
            numerator = (all_long_entry - short_entry_value + long_maintenance
                         + short_maintenance + other_cross_maintenance
                         + other_cross_liquidation_fee + total_entry_fees
                         + funding_cost - cross_buffer)
            denominator = target_net_quantity - liquidation_fee_rate * target_total_quantity
            if denominator <= 0:
                raise GridError(
                    "При текущих встречных позициях нельзя получить надёжную оценку LONG-ликвидации."
                )
            liquidation = numerator / denominator

        if not math.isfinite(liquidation):
            raise GridError("MEXC вернула данные, по которым нельзя рассчитать ликвидацию.")

        average = all_long_entry / all_long_quantity
        rows.append({
            "step": step,
            "price": price,
            "margin": margin,
            "actual_margin": actual_margin,
            "contracts": volume,
            "notional": entry_value,
            "avg": average,
            "liq": liquidation,
            "mmr": all_long_rate,
            "fee": entry_fee,
            "funding": funding_cost,
            "funding_events": funding_events,
        })

        if step < len(MARGINS):
            price = average - ENTRY_COEF * (average - liquidation)
            if price <= liquidation or price <= 0:
                raise GridError("Следующий вход не выше расчётной ликвидации предыдущего шага.")

    first_price = rows[0]["price"]
    last_price = rows[-1]["price"]
    span = first_price - last_price
    for row in rows:
        row["pct"] = ((first_price - row["price"]) / span * 100) if span else 0.0

    if rows[0]["price"] <= rows[0]["liq"]:
        raise GridError("Ликвидация первой позиции находится выше цены первого входа.")
    if rows[-1]["price"] <= rows[-2]["liq"]:
        raise GridError("Цена входа шага 4 не выше расчётной ликвидации шага 3.")

    budget_margin = sum(row["actual_margin"] for row in rows)
    budget_entry_fees = sum(row["fee"] for row in rows)
    budget_funding = rows[-1]["funding"]
    budget_required = budget_margin + budget_entry_fees + budget_funding
    return {
        "leverage": leverage,
        "open_type": opened_mode,
        "mode": "изолированная" if opened_mode == 1 else "кросс-маржа",
        "fair_price": fair_price,
        "funding_rate": _num(funding.get("fundingRate"), "funding.fundingRate"),
        "funding_stress_rate": funding_rate,
        "funding_events": funding_events,
        "funding_cycle": _int(funding.get("collectCycle"), "funding.collectCycle"),
        "hours": hours,
        "fee_rate": fee_rate,
        "liquidation_fee_rate": liquidation_fee_rate,
        "mmr": rows[-1]["mmr"],
        "account_available": cash_available,
        "budget_limit": GRID_BUDGET,
        "budget_margin": budget_margin,
        "budget_entry_fees": budget_entry_fees,
        "budget_funding": budget_funding,
        "budget_required": budget_required,
        "margin_scale": margin_scale,
        "current_liquidation": current_isolated_liq,
        "rows": rows,
        "contract_size": contract_size,
        "places": _int(contract.get("priceScale"), "priceScale", 4),
        "volume_places": _int(contract.get("volScale"), "volScale", 0),
        "fetched_at": context.get("fetched_at"),
    }


def _fit_grid_budget(context: dict, p1: float, leverage: int, hours: float,
                     opened_mode: int, target_positions: list[dict]) -> dict:
    account = _asset_usdt(context)
    available_value = account.get("availableOpen")
    if available_value is None:
        available_value = account.get("availableBalance")
    available = _num(available_value, "account.availableOpen")
    if available + 1e-9 < GRID_BUDGET:
        raise GridError("Для запуска сетки на 10 USDT нужно не менее 10 USDT доступного баланса.")

    last_error = None
    valid_candidate = False
    for index in range(500):
        margin_scale = 1.0 - index * MARGIN_SCALE_STEP
        if margin_scale <= 0:
            break
        try:
            result = _calculate_for_leverage(
                context, p1, leverage, hours, opened_mode, target_positions,
                margin_scale=margin_scale,
            )
        except GridError as exc:
            last_error = exc
            continue
        valid_candidate = True
        if result["budget_required"] <= GRID_BUDGET + 1e-9:
            result["budget_limit"] = GRID_BUDGET
            return result

    if valid_candidate:
        raise GridError(
            "Четыре шага не помещаются в доступные 10 USDT с учётом комиссии и резерва на фандинг."
        )
    if last_error:
        raise last_error
    raise GridError("Не удалось подобрать размеры четырёх шагов в бюджете MEXC.")


def calculate_exchange_grid(p1_text: str, *, context: dict, leverage: int | None = None,
                            target: float | None = None,
                            hours_until_step4: float = DEFAULT_HOURS_UNTIL_STEP4) -> dict:
    try:
        p1 = float(str(p1_text or context.get("fair_price", "")).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise GridError("Введите цену первого входа.") from exc
    if not math.isfinite(p1) or p1 <= 0:
        raise GridError("Цена первого входа должна быть больше нуля.")
    try:
        hours = float(hours_until_step4)
    except (TypeError, ValueError) as exc:
        raise GridError("Введите срок до шага 4 в часах.") from exc
    if not math.isfinite(hours) or not 0 <= hours <= 24 * 30:
        raise GridError("Срок должен быть от 0 до 720 часов.")

    symbol = context["symbol"]
    target_positions = [
        row for row in context.get("positions", [])
        if _active(row) and str(row.get("symbol", "")) == symbol
    ]
    open_type, current_lev, min_lev, max_lev = _leverage_settings(context, target_positions)
    _check_market_freshness(context, context.get("funding") or {})
    _check_pending_orders(context)

    if target is None:
        selected = current_lev if leverage is None else _int(leverage, "leverage")
        if not min_lev <= selected <= max_lev:
            raise GridError(f"Для этого аккаунта MEXC плечо должно быть от {min_lev}x до {max_lev}x.")
        result = _fit_grid_budget(
            context, p1, selected, hours, open_type, target_positions
        )
        result["target"] = None
        result["max_leverage"] = max_lev
        return result

    try:
        target = float(str(target).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise GridError("Введите целевую ликвидацию после шага 4.") from exc
    if not math.isfinite(target) or target <= 0 or target >= p1:
        raise GridError("Целевая ликвидация должна быть ниже цены первого входа.")
    if target_positions:
        raise GridError("Нельзя подобрать другое плечо, пока по этой паре открыта позиция.")

    candidates = []
    for candidate in range(min_lev, max_lev + 1):
        try:
            item = _fit_grid_budget(context, p1, candidate, hours, open_type, [])
            candidates.append(item)
        except GridError:
            continue
    if not candidates:
        raise GridError("Не удалось построить сетку в пределах лимитов MEXC.")
    result = min(candidates, key=lambda item: abs(item["rows"][-1]["liq"] - target))
    result["target"] = target
    result["max_leverage"] = max_lev
    return result
