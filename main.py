#!/usr/bin/env python3
"""
Forex Scanner — main entry point.
Runs the scanner on a schedule and serves the Flask API in a background thread.
"""

import os
import sys
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import schedule
import yaml
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.data_fetcher import DataFetcher
from core.environment import EnvironmentAnalyser
from core.strategy_engine import StrategyEngine
from core.signals import init_db, save_signal, get_unalerted_entry_ready, mark_alerted
from alerts.email_alert import send_email
from api.server import run_server, set_scanner, update_scan_times, set_live_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ── Config loading ────────────────────────────────────────────────────

def load_config() -> dict:
    cfg_path = Path(__file__).parent / "config" / "strategies.yaml"
    with open(cfg_path, "r") as f:
        return yaml.safe_load(f)


# ── Session filter ────────────────────────────────────────────────────

def in_active_session(config: dict) -> bool:
    sf = config.get("session_filter", {})
    if not sf.get("enabled", False):
        return True
    now_utc = datetime.now(timezone.utc).time()

    def in_window(start_str, end_str):
        s = datetime.strptime(start_str, "%H:%M").time()
        e = datetime.strptime(end_str, "%H:%M").time()
        return s <= now_utc <= e

    for session in sf.get("sessions", {}).values():
        if in_window(session["start_utc"], session["end_utc"]):
            return True
    return False


# ── Scanner ───────────────────────────────────────────────────────────

class Scanner:
    def __init__(self, config: dict):
        self.config = config
        self.fetcher = DataFetcher()
        self.analyser = EnvironmentAnalyser()
        self.engine = StrategyEngine(config)

    def run_scan(self) -> list:
        pairs = self.config.get("watchlist", [])
        all_results = []
        live_state = []

        for pair in pairs:
            try:
                data = self.fetcher.fetch_all(pair)
            except Exception as e:
                logger.error(f"Failed to fetch data for {pair}: {e}")
                live_state.append({"pair": pair, "error": str(e), "scanned_at": datetime.utcnow().isoformat()})
                continue

            env = self.analyser.analyse(
                pair,
                data["candles_4h"],
                data["ema10_4h"],
                data["ema20_4h"],
                data["ema50_4h"],
            )

            env_dict = {
                "trend_state": env.trend_state,
                "ma_stack_bullish": bool(env.ma_stack_bullish),
                "ma_stack_bearish": bool(env.ma_stack_bearish),
                "emas_rising": bool(env.emas_rising),
                "structure_intact": bool(env.structure_intact),
                "suitable_for_long": bool(env.suitable_for_long),
                "detail": {k: float(v) if hasattr(v, '__float__') else v for k, v in env.detail.items()},
            }

            if not env.suitable_for_long:
                logger.info(f"{pair} | FILTERED by environment ({env.trend_state})")
                live_state.append({
                    "pair": pair,
                    "filtered": True,
                    "environment": env_dict,
                    "stage": "filtered",
                    "strategy_results": [],
                    "scanned_at": datetime.utcnow().isoformat(),
                })
                continue

            results = self.engine.evaluate(pair, env, data)
            strategy_results = []
            for r in results:
                save_signal(r)
                stage_disp = r.stage.upper().replace("_", " ")
                price_disp = f"{r.buy_stop_price:.5f}" if r.buy_stop_price else "—"
                logger.info(
                    f"{pair:10s} | {env.trend_state:12s} | {stage_disp:15s} | {price_disp}"
                )
                strategy_results.append({
                    "strategy_name": r.strategy_name,
                    "stage": r.stage,
                    "context_conditions_met": r.context_conditions_met,
                    "context_passed": r.context_passed,
                    "entry_conditions_met": r.entry_conditions_met,
                    "entry_passed": r.entry_passed,
                    "buy_stop_price": r.buy_stop_price,
                })
            all_results.extend(results)

            stage = results[0].stage if results else "not_applicable"
            live_state.append({
                "pair": pair,
                "filtered": False,
                "environment": env_dict,
                "stage": stage,
                "strategy_results": strategy_results,
                "scanned_at": datetime.utcnow().isoformat(),
            })

        set_live_state(live_state)
        return all_results

    def send_pending_alerts(self):
        pending = get_unalerted_entry_ready()
        for sig in pending:
            if send_email(sig):
                mark_alerted(sig["id"])


# ── Scheduler tick ────────────────────────────────────────────────────

def scan_tick(scanner: Scanner, config: dict):
    if not in_active_session(config):
        logger.info("Outside active session — scan skipped")
        return

    logger.info("=" * 60)
    logger.info(f"SCAN START — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    try:
        scanner.run_scan()
        scanner.send_pending_alerts()
    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)

    now = datetime.now(timezone.utc)
    interval = config.get("scan_interval_minutes", 15)
    next_scan = now + timedelta(minutes=interval)
    update_scan_times(now, next_scan)
    logger.info(f"Next scan at {next_scan.strftime('%H:%M UTC')}")


# ── Startup banner ────────────────────────────────────────────────────

def print_banner(config: dict, scanner: Scanner):
    pairs = config.get("watchlist", [])
    strategies = {k: v for k, v in config.get("strategies", {}).items() if v.get("enabled")}
    interval = config.get("scan_interval_minutes", 15)
    sf = config.get("session_filter", {})
    session_on = sf.get("enabled", False)

    print()
    print("=" * 48)
    print("         FOREX STRATEGY SCANNER")
    print("=" * 48)
    print(f"  Strategies loaded : {len(strategies)}")
    print(f"  Watched pairs     : {', '.join(pairs)}")
    print(f"  Scan interval     : {interval} min")
    print(f"  Session filter    : {'ON (London + NY)' if session_on else 'OFF (always scan)'}")
    print(f"  Dashboard         : http://localhost:5001")
    print("=" * 48)
    print()


# ── Main ──────────────────────────────────────────────────────────────

def main():
    config = load_config()
    init_db()

    scanner = Scanner(config)
    set_scanner(scanner)

    print_banner(config, scanner)

    # Start Flask in background thread
    flask_thread = threading.Thread(
        target=run_server,
        kwargs={"host": "0.0.0.0", "port": 5001, "debug": False},
        daemon=True,
    )
    flask_thread.start()
    logger.info("Flask API server started on port 5000")

    # Initial scan immediately
    scan_tick(scanner, config)

    # Schedule recurring scans
    interval = config.get("scan_interval_minutes", 15)
    schedule.every(interval).minutes.do(scan_tick, scanner=scanner, config=config)
    logger.info(f"Scheduler running every {interval} minutes")

    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
