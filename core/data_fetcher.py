import os
import time
import logging
import requests
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

INTERVAL_MAP = {
    "4H": "4h",
    "30min": "30min",
    "1H": "1h",
    "1D": "1day",
}


class DataFetcher:
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("TWELVE_DATA_API_KEY", "")
        if not self.api_key:
            raise ValueError("TWELVE_DATA_API_KEY is not set")
        self._cache: dict = {}  # key -> (timestamp, data)
        self._cache_ttl = 600  # 10 minutes

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def _cache_get(self, key: str):
        if key in self._cache:
            ts, data = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return data
            del self._cache[key]
        return None

    def _cache_set(self, key: str, data):
        self._cache[key] = (time.time(), data)

    # ------------------------------------------------------------------
    # HTTP request with retry + backoff
    # ------------------------------------------------------------------
    def _get(self, endpoint: str, params: dict, retries: int = 3) -> dict:
        params["apikey"] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}"
        for attempt in range(retries):
            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "error":
                    msg = data.get("message", "Unknown API error")
                    if "rate limit" in msg.lower():
                        wait = 2 ** attempt * 5
                        logger.warning(f"Rate limit hit — waiting {wait}s")
                        time.sleep(wait)
                        continue
                    raise RuntimeError(f"Twelve Data API error: {msg}")
                return data
            except requests.RequestException as e:
                wait = 2 ** attempt * 2
                logger.warning(f"Request failed ({e}), retry in {wait}s")
                if attempt < retries - 1:
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"All {retries} retries exhausted for {endpoint}")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def get_candles(self, pair: str, interval: str, outputsize: int = 100) -> pd.DataFrame:
        td_interval = INTERVAL_MAP.get(interval, interval)
        cache_key = f"candles:{pair}:{td_interval}:{outputsize}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return cached

        data = self._get("time_series", {
            "symbol": pair,
            "interval": td_interval,
            "outputsize": outputsize,
            "order": "ASC",
        })

        values = data.get("values", [])
        if not values:
            raise ValueError(f"No candle data returned for {pair} {interval}")

        df = pd.DataFrame(values)
        df = df.rename(columns={"datetime": "time"})
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)

        self._cache_set(cache_key, df)
        logger.info(f"Fetched {len(df)} candles for {pair} {interval}")
        return df

    @staticmethod
    def compute_ema(df: pd.DataFrame, period: int) -> pd.Series:
        """Compute EMA locally from candle close prices — no extra API call needed."""
        return df["close"].ewm(span=period, adjust=False).mean()

    def fetch_all(self, pair: str) -> dict:
        """Fetch candles for both timeframes and compute EMAs locally.
        Only 2 API calls per pair instead of 7 — stays within free tier limits."""
        candles_4h = self.get_candles(pair, "4H")
        candles_30m = self.get_candles(pair, "30min")

        # Compute EMAs locally from close prices
        ema10_4h = self.compute_ema(candles_4h, 10)
        ema20_4h = self.compute_ema(candles_4h, 20)
        ema50_4h = self.compute_ema(candles_4h, 50)
        ema10_30m = self.compute_ema(candles_30m, 10)
        ema20_30m = self.compute_ema(candles_30m, 20)

        # Convert to Series indexed the same way the strategy engine expects
        def to_series(ema: pd.Series, df: pd.DataFrame) -> pd.Series:
            return pd.Series(ema.values, index=df["time"])

        return {
            "candles_4h": candles_4h,
            "candles_30m": candles_30m,
            "ema10_4h": to_series(ema10_4h, candles_4h),
            "ema20_4h": to_series(ema20_4h, candles_4h),
            "ema50_4h": to_series(ema50_4h, candles_4h),
            "ema10_30m": to_series(ema10_30m, candles_30m),
            "ema20_30m": to_series(ema20_30m, candles_30m),
        }

    def check_connectivity(self) -> bool:
        try:
            self._get("api_usage", {})
            return True
        except Exception as e:
            logger.error(f"API connectivity check failed: {e}")
            return False
