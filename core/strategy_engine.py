import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Tuple

from core.environment import EnvironmentState

logger = logging.getLogger(__name__)

# Pip sizes per pair (price units per pip)
PIP_SIZE = {
    "USD/JPY": 0.01,
    "EUR/JPY": 0.01,
    "GBP/JPY": 0.01,
    "DEFAULT": 0.0001,
}


def pip_to_price(pair: str, pips: float) -> float:
    size = PIP_SIZE.get(pair.upper(), PIP_SIZE["DEFAULT"])
    return pips * size


@dataclass
class SignalResult:
    pair: str
    strategy_name: str
    timestamp: datetime
    environment: EnvironmentState
    context_conditions_met: dict = field(default_factory=dict)
    context_passed: bool = False
    entry_conditions_met: dict = field(default_factory=dict)
    entry_passed: bool = False
    buy_stop_price: Optional[float] = None
    stage: str = "not_applicable"
    invalidated: bool = False


class StrategyEngine:

    def __init__(self, config: dict):
        self.config = config
        self.strategies = config.get("strategies", {})

    def evaluate(
        self,
        pair: str,
        environment: EnvironmentState,
        data: dict,
    ) -> List[SignalResult]:
        results = []
        for strat_id, strat_cfg in self.strategies.items():
            if not strat_cfg.get("enabled", False):
                continue
            result = self._evaluate_strategy(pair, strat_id, strat_cfg, environment, data)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Single strategy evaluation
    # ------------------------------------------------------------------
    def _evaluate_strategy(
        self,
        pair: str,
        strat_id: str,
        cfg: dict,
        environment: EnvironmentState,
        data: dict,
    ) -> SignalResult:
        result = SignalResult(
            pair=pair,
            strategy_name=cfg.get("name", strat_id),
            timestamp=datetime.utcnow(),
            environment=environment,
        )

        # --- Environment gate ---
        env_filter = cfg.get("environment_filter", {})
        if env_filter.get("suitable_for_long") and not environment.suitable_for_long:
            logger.debug(f"{pair}/{strat_id}: filtered by environment")
            result.stage = "not_applicable"
            return result

        # --- Context phase (4H) ---
        ctx_cfg = cfg.get("context_phase", {})
        ctx_tf = ctx_cfg.get("timeframe", "4H")
        candles_ctx = self._get_candles(data, ctx_tf)
        emas_ctx = self._get_emas(data, ctx_tf)
        ctx_conditions = ctx_cfg.get("conditions", {})

        ctx_met, ctx_push_candle = self._evaluate_conditions(
            pair, ctx_conditions, candles_ctx, emas_ctx
        )
        result.context_conditions_met = {k: v for k, v in ctx_met.items()}
        result.context_passed = all(ctx_met.values())

        if not result.context_passed:
            result.stage = "not_applicable"
            return result

        # --- Entry phase (30min) ---
        ent_cfg = cfg.get("entry_phase", {})
        ent_tf = ent_cfg.get("timeframe", "30min")
        candles_ent = self._get_candles(data, ent_tf)
        emas_ent = self._get_emas(data, ent_tf)
        ent_conditions = ent_cfg.get("conditions", {})

        ent_met, ent_push_candle = self._evaluate_conditions(
            pair, ent_conditions, candles_ent, emas_ent
        )
        result.entry_conditions_met = {k: v for k, v in ent_met.items()}
        result.entry_passed = all(ent_met.values())

        if result.entry_passed:
            result.stage = "entry_ready"
            # Buy stop = high of the push candle in entry phase
            if ent_push_candle is not None:
                result.buy_stop_price = float(ent_push_candle["high"])
        else:
            # Context passed but entry forming
            result.stage = "watching"

        return result

    # ------------------------------------------------------------------
    # Condition dispatch
    # ------------------------------------------------------------------
    def _evaluate_conditions(
        self,
        pair: str,
        conditions: dict,
        candles: pd.DataFrame,
        emas: dict,
    ) -> Tuple[dict, Optional[pd.Series]]:
        met = {}
        push_candle = None

        for cond_id, cond_cfg in conditions.items():
            ctype = cond_cfg.get("type")
            try:
                if ctype == "price_below_all_emas":
                    met[cond_id] = self._check_price_below_emas(candles, emas, cond_cfg)
                elif ctype == "atr_contraction":
                    met[cond_id] = self._check_atr_contraction(candles, cond_cfg)
                elif ctype == "cross_above_emas":
                    ok, candle = self._check_cross_above_emas(candles, emas, cond_cfg)
                    met[cond_id] = ok
                    if ok and candle is not None:
                        push_candle = candle
                elif ctype == "retest_ema_zone":
                    met[cond_id] = self._check_retest_ema_zone(pair, candles, emas, cond_cfg)
                else:
                    logger.warning(f"Unknown condition type: {ctype}")
                    met[cond_id] = False
            except Exception as e:
                logger.error(f"Error evaluating condition {cond_id}: {e}")
                met[cond_id] = False

        return met, push_candle

    # ------------------------------------------------------------------
    # Condition implementations
    # ------------------------------------------------------------------
    def _check_price_below_emas(
        self, candles: pd.DataFrame, emas: dict, cfg: dict
    ) -> bool:
        """Price closes below all specified EMAs for at least min_bars."""
        periods = cfg.get("emas", [10, 20])
        min_bars = cfg.get("min_bars", 5)
        df = candles.iloc[:-1]  # exclude forming candle

        count = 0
        for i in range(len(df) - 1, max(len(df) - min_bars * 3, -1), -1):
            row = df.iloc[i]
            all_below = all(
                row["close"] < self._ema_at_index(emas, p, i, len(df))
                for p in periods
            )
            if all_below:
                count += 1
                if count >= min_bars:
                    return True
            else:
                break  # must be consecutive (most recent)
        return count >= min_bars

    def _check_atr_contraction(self, candles: pd.DataFrame, cfg: dict) -> bool:
        """ATR now is threshold fraction or less of ATR at start of window."""
        lookback = cfg.get("lookback", 8)
        threshold = cfg.get("threshold", 0.6)
        df = candles.iloc[:-1]
        if len(df) < lookback + 1:
            return False

        # Simple ATR using high-low range
        recent = df.iloc[-lookback:]
        ranges = (recent["high"] - recent["low"]).values

        atr_start = float(np.mean(ranges[:3]))  # first 3 bars of window
        atr_now = float(np.mean(ranges[-3:]))   # last 3 bars of window

        if atr_start == 0:
            return False
        ratio = atr_now / atr_start
        return ratio <= threshold

    def _check_cross_above_emas(
        self, candles: pd.DataFrame, emas: dict, cfg: dict
    ) -> Tuple[bool, Optional[pd.Series]]:
        """Bullish candle closes above all specified EMAs with required body ratio."""
        periods = cfg.get("emas", [10, 20])
        min_body = cfg.get("min_body_ratio", 0.6)
        df = candles.iloc[:-1]

        # Check last 5 bars for a qualifying candle
        for i in range(len(df) - 1, max(len(df) - 6, -1), -1):
            row = df.iloc[i]
            candle_range = row["high"] - row["low"]
            if candle_range == 0:
                continue
            body = abs(row["close"] - row["open"])
            body_ratio = body / candle_range

            bullish = row["close"] > row["open"]
            good_body = body_ratio >= min_body
            above_emas = all(
                row["close"] > self._ema_at_index(emas, p, i, len(df))
                for p in periods
            )
            # Previous bar must have been below at least one EMA (the cross)
            if i > 0:
                prev = df.iloc[i - 1]
                prev_below = any(
                    prev["close"] < self._ema_at_index(emas, p, i - 1, len(df))
                    for p in periods
                )
            else:
                prev_below = True

            if bullish and good_body and above_emas and prev_below:
                return True, row
        return False, None

    def _check_retest_ema_zone(
        self, pair: str, candles: pd.DataFrame, emas: dict, cfg: dict
    ) -> bool:
        """Price returns to touch the EMA zone after moving above it."""
        periods = cfg.get("emas", [10, 20])
        must_be_rising = cfg.get("emas_must_be_rising", True)
        tol_pips = cfg.get("tolerance_pips", 10)
        tol = pip_to_price(pair, tol_pips)

        df = candles.iloc[:-1]
        if len(df) < 5:
            return False

        # Check last 5 bars for a retest candle
        for i in range(len(df) - 1, max(len(df) - 6, -1), -1):
            row = df.iloc[i]
            ema_vals = [self._ema_at_index(emas, p, i, len(df)) for p in periods]
            ema_zone_top = max(ema_vals)
            ema_zone_bot = min(ema_vals)

            low_touches_zone = row["low"] <= ema_zone_top + tol
            close_above_zone = row["close"] >= ema_zone_bot - tol

            if low_touches_zone and close_above_zone:
                if must_be_rising:
                    # All EMAs rising (last bar vs 5 bars ago)
                    rising = all(
                        self._ema_rising(emas, p, i, len(df))
                        for p in periods
                    )
                    if rising:
                        return True
                else:
                    return True
        return False

    # ------------------------------------------------------------------
    # EMA lookup helpers
    # ------------------------------------------------------------------
    def _ema_at_index(self, emas: dict, period: int, candle_idx: int, total: int) -> float:
        """Get EMA value aligned to candle index."""
        key = period
        series = emas.get(key)
        if series is None:
            return 0.0
        vals = series.values
        # Align: last EMA value = last candle
        offset = len(vals) - total
        idx = offset + candle_idx
        if 0 <= idx < len(vals):
            return float(vals[idx])
        return float(vals[-1]) if len(vals) else 0.0

    def _ema_rising(self, emas: dict, period: int, idx: int, total: int) -> bool:
        v_now = self._ema_at_index(emas, period, idx, total)
        v_past = self._ema_at_index(emas, period, max(idx - 5, 0), total)
        return v_now > v_past

    # ------------------------------------------------------------------
    # Data accessors
    # ------------------------------------------------------------------
    def _get_candles(self, data: dict, timeframe: str) -> pd.DataFrame:
        key = "candles_4h" if timeframe == "4H" else "candles_30m"
        return data[key]

    def _get_emas(self, data: dict, timeframe: str) -> dict:
        if timeframe == "4H":
            return {
                10: data["ema10_4h"],
                20: data["ema20_4h"],
                50: data.get("ema50_4h"),
            }
        else:
            return {
                10: data["ema10_30m"],
                20: data["ema20_30m"],
            }
