import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EnvironmentState:
    pair: str
    trend_state: str          # "trending_up" | "trending_down" | "ranging"
    ma_stack_bullish: bool    # EMA10 > EMA20 > EMA50 on 4H
    ma_stack_bearish: bool    # EMA10 < EMA20 < EMA50 on 4H
    emas_rising: bool         # All 3 EMAs have positive slope over last 5 bars
    structure_intact: bool    # Higher highs and higher lows sequence intact
    suitable_for_long: bool   # True when trending_up + ma_stack_bullish + emas_rising
    detail: dict = field(default_factory=dict)


class EnvironmentAnalyser:

    def analyse(
        self,
        pair: str,
        candles_4h: pd.DataFrame,
        ema10_4h: pd.Series,
        ema20_4h: pd.Series,
        ema50_4h: pd.Series,
    ) -> EnvironmentState:

        # Use completed candles only (exclude last forming bar)
        df = candles_4h.iloc[:-1].copy().reset_index(drop=True)

        # Align EMA series to candle index (last N values)
        e10 = ema10_4h.values[-len(df):]
        e20 = ema20_4h.values[-len(df):]
        e50 = ema50_4h.values[-len(df):]

        # --- Swing pivot detection (5-bar window: need at least 5 bars) ---
        swing_highs, swing_lows = self._find_pivots(df)

        trend_state = self._classify_trend(swing_highs, swing_lows)
        structure_intact = self._check_structure(swing_highs, swing_lows, trend_state)

        # --- MA stack ---
        last_e10 = float(e10[-1]) if len(e10) else 0.0
        last_e20 = float(e20[-1]) if len(e20) else 0.0
        last_e50 = float(e50[-1]) if len(e50) else 0.0

        ma_stack_bullish = last_e10 > last_e20 > last_e50
        ma_stack_bearish = last_e10 < last_e20 < last_e50

        # --- EMAs rising (slope over last 5 bars) ---
        emas_rising = False
        if len(e10) >= 5 and len(e20) >= 5 and len(e50) >= 5:
            emas_rising = (
                float(e10[-1]) > float(e10[-5]) and
                float(e20[-1]) > float(e20[-5]) and
                float(e50[-1]) > float(e50[-5])
            )

        suitable_for_long = (
            trend_state == "trending_up" and
            ma_stack_bullish and
            emas_rising
        )

        detail = {
            "swing_highs": [float(v) for v in swing_highs[-3:]],
            "swing_lows": [float(v) for v in swing_lows[-3:]],
            "ema10": round(last_e10, 5),
            "ema20": round(last_e20, 5),
            "ema50": round(last_e50, 5),
        }

        state = EnvironmentState(
            pair=pair,
            trend_state=trend_state,
            ma_stack_bullish=ma_stack_bullish,
            ma_stack_bearish=ma_stack_bearish,
            emas_rising=emas_rising,
            structure_intact=structure_intact,
            suitable_for_long=suitable_for_long,
            detail=detail,
        )
        logger.info(
            f"{pair} env: trend={trend_state}, stack_bull={ma_stack_bullish}, "
            f"emas_rising={emas_rising}, suitable_long={suitable_for_long}"
        )
        return state

    # ------------------------------------------------------------------
    # Pivot detection — 5-bar centred window
    # ------------------------------------------------------------------
    def _find_pivots(self, df: pd.DataFrame):
        highs = df["high"].values
        lows = df["low"].values
        n = len(highs)

        swing_highs = []
        swing_lows = []

        for i in range(2, n - 2):
            window_h = highs[i - 2: i + 3]
            window_l = lows[i - 2: i + 3]
            if highs[i] == max(window_h):
                swing_highs.append(highs[i])
            if lows[i] == min(window_l):
                swing_lows.append(lows[i])

        return swing_highs, swing_lows

    # ------------------------------------------------------------------
    # Trend classification
    # ------------------------------------------------------------------
    def _classify_trend(self, swing_highs: list, swing_lows: list) -> str:
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "ranging"

        hh = swing_highs[-1] > swing_highs[-2]   # higher high
        hl = swing_lows[-1] > swing_lows[-2]      # higher low
        lh = swing_highs[-1] < swing_highs[-2]    # lower high
        ll = swing_lows[-1] < swing_lows[-2]      # lower low

        if hh and hl:
            return "trending_up"
        if lh and ll:
            return "trending_down"
        return "ranging"

    # ------------------------------------------------------------------
    # Structure check
    # ------------------------------------------------------------------
    def _check_structure(self, swing_highs: list, swing_lows: list, trend: str) -> bool:
        if trend == "trending_up":
            if len(swing_highs) >= 2 and len(swing_lows) >= 2:
                return swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2]
        elif trend == "trending_down":
            if len(swing_highs) >= 2 and len(swing_lows) >= 2:
                return swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2]
        return False
