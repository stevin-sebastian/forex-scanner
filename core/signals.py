import sqlite3
import json
import logging
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
from typing import Optional, List

from core.strategy_engine import SignalResult

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "signals.db"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT,
    strategy TEXT,
    stage TEXT,
    buy_stop_price REAL,
    fired_at TEXT,
    conditions_json TEXT,
    environment_json TEXT,
    alerted INTEGER DEFAULT 0
);
"""


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(CREATE_TABLE)
        conn.commit()
    logger.info(f"Database initialised at {DB_PATH}")


def _env_to_dict(env) -> dict:
    return {
        "pair": env.pair,
        "trend_state": env.trend_state,
        "ma_stack_bullish": env.ma_stack_bullish,
        "ma_stack_bearish": env.ma_stack_bearish,
        "emas_rising": env.emas_rising,
        "structure_intact": env.structure_intact,
        "suitable_for_long": env.suitable_for_long,
        "detail": env.detail,
    }


def _conditions_to_dict(result: SignalResult) -> dict:
    return {
        "context": result.context_conditions_met,
        "entry": result.entry_conditions_met,
    }


def get_last_stage(pair: str, strategy: str) -> Optional[str]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT stage FROM signals WHERE pair=? AND strategy=? ORDER BY id DESC LIMIT 1",
            (pair, strategy),
        ).fetchone()
    return row[0] if row else None


def should_persist(result: SignalResult) -> bool:
    """Only save when stage changes or a fresh entry_ready fires."""
    last = get_last_stage(result.pair, result.strategy_name)
    if last is None:
        return result.stage != "not_applicable"
    if result.stage == "not_applicable":
        return False
    return result.stage != last or result.stage == "entry_ready"


def save_signal(result: SignalResult) -> Optional[int]:
    if not should_persist(result):
        return None
    conditions_json = json.dumps(_conditions_to_dict(result))
    environment_json = json.dumps(_env_to_dict(result.environment))
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """INSERT INTO signals
               (pair, strategy, stage, buy_stop_price, fired_at, conditions_json, environment_json, alerted)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                result.pair,
                result.strategy_name,
                result.stage,
                result.buy_stop_price,
                result.timestamp.isoformat(),
                conditions_json,
                environment_json,
            ),
        )
        conn.commit()
        logger.info(f"Saved signal: {result.pair} | {result.strategy_name} | {result.stage}")
        return cursor.lastrowid


def mark_alerted(signal_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE signals SET alerted=1 WHERE id=?", (signal_id,))
        conn.commit()


def get_unalerted_entry_ready() -> List[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals WHERE stage='entry_ready' AND alerted=0 ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_signals(limit: int = 50) -> List[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["conditions_json"] = json.loads(d["conditions_json"])
        except Exception:
            pass
        try:
            d["environment_json"] = json.loads(d["environment_json"])
        except Exception:
            pass
        result.append(d)
    return result
