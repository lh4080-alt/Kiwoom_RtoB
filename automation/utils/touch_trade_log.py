"""
touch 거래 로그 SQLite — MAE/MFE 갱신형, 거래별 파라미터 스냅샷 + 슬리피지 추적.

영구 원칙 #30: 봇 데몬 내부에서만 조작. 외부 프로세스 직접 write 금지.

스키마 설계 근거:
- ts_trigger vs ts_entry: 트리거 충족 시점과 체결 시점 분리 → 슬리피지 측정
- trigger_price vs entry_price: 계산된 트리거가와 실제 진입가 → 시장가 진입 슬리피지
- initial_low: 무효화 가드 기준 최초 저가 (갱신되는 low와 분리)
- 파라미터 스냅샷 6개: 운영 중 set 명령으로 값 바뀌므로 거래별 박아둠 →
  파라미터 변경 시점 전후 데이터 섞임 방지 (분석 오염 차단)
- MAE/MFE 갱신형: 진입 후 최저/최고가 + 도달 ts만 update.
  풀 틱 저장은 row 폭증 → 30건 분석엔 과함.
- exit_reason enum: stop_loss / take_profit / closing_auction_1520 / manual
  → 청산 경로별 분석 (-2% 손절이 노이즈였나 추세였나)

체결 미확정 처리:
- rc=0 + ord_no 받은 시점에 insert (시장가는 거의 즉시 체결 가정)
- rc≠0 또는 미체결은 insert 안 함 (분석 오염 방지)
"""
import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'trade_log.db')

_lock = asyncio.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS touch_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    ord_no TEXT,

    -- 진입 정보
    ts_trigger TEXT NOT NULL,
    ts_entry TEXT NOT NULL,
    entry_price REAL NOT NULL,
    qty INTEGER NOT NULL,

    -- 트리거 시점 시장 상태
    open_prc REAL,
    low REAL,
    initial_low REAL,
    drop_pct REAL,
    trigger_price REAL,
    cntr_str_5min REAL,

    -- 진입 당시 파라미터 스냅샷
    param_touch_rate REAL,
    param_min_drop_pct REAL,
    param_min_strength REAL,
    param_invalidate_pct REAL,
    param_stop_loss_pct REAL,
    param_take_profit_pct REAL,

    -- 청산 정보 (NULL이면 미청산)
    ts_exit TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl_won INTEGER,
    pnl_pct REAL,

    -- MAE/MFE 갱신형
    mae_price REAL,
    mae_ts TEXT,
    mae_pct REAL,
    mfe_price REAL,
    mfe_ts TEXT,
    mfe_pct REAL,

    UNIQUE(code, ord_no)
);

CREATE INDEX IF NOT EXISTS idx_touch_trades_code ON touch_trades(code);
CREATE INDEX IF NOT EXISTS idx_touch_trades_entry ON touch_trades(ts_entry);
CREATE INDEX IF NOT EXISTS idx_touch_trades_exit ON touch_trades(exit_reason);
CREATE INDEX IF NOT EXISTS idx_touch_trades_open ON touch_trades(ts_exit) WHERE ts_exit IS NULL;
"""


def _conn() -> sqlite3.Connection:
	"""SQLite 연결. DB 폴더 자동 생성."""
	os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
	c = sqlite3.connect(_DB_PATH)
	c.row_factory = sqlite3.Row
	return c


def _init_sync():
	with _conn() as c:
		c.executescript(_SCHEMA)


async def init_db():
	"""봇 startup 시 1회 호출."""
	async with _lock:
		_init_sync()


async def insert_entry(
	*,
	code: str,
	ord_no: str,
	ts_trigger: str,
	ts_entry: str,
	entry_price: float,
	qty: int,
	open_prc: float,
	low: float,
	initial_low: float,
	drop_pct: float,
	trigger_price: float,
	cntr_str_5min: float,
	param_touch_rate: float,
	param_min_drop_pct: float,
	param_min_strength: float,
	param_invalidate_pct: float,
	param_stop_loss_pct: float,
	param_take_profit_pct: float,
) -> Optional[int]:
	"""체결 확정 시점에 1회 호출. trade_id 반환 (중복이면 None).

	rc=0 + ord_no 받은 시점에만 호출 — 미체결/실패는 부르지 않음.
	"""
	async with _lock:
		try:
			with _conn() as c:
				cur = c.execute(
					"""INSERT OR IGNORE INTO touch_trades (
						code, ord_no, ts_trigger, ts_entry, entry_price, qty,
						open_prc, low, initial_low, drop_pct, trigger_price, cntr_str_5min,
						param_touch_rate, param_min_drop_pct, param_min_strength,
						param_invalidate_pct, param_stop_loss_pct, param_take_profit_pct,
						mae_price, mae_ts, mae_pct, mfe_price, mfe_ts, mfe_pct
					) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
					(
						code, ord_no, ts_trigger, ts_entry, entry_price, qty,
						open_prc, low, initial_low, drop_pct, trigger_price, cntr_str_5min,
						param_touch_rate, param_min_drop_pct, param_min_strength,
						param_invalidate_pct, param_stop_loss_pct, param_take_profit_pct,
						# MAE/MFE 초기값 = 진입가 (역행/순행 0)
						entry_price, ts_entry, 0.0,
						entry_price, ts_entry, 0.0,
					),
				)
				if cur.rowcount == 0:
					logger.info(f"[trade_log] {code} ord_no={ord_no} 이미 존재 — 중복 insert 무시")
					return None
				return cur.lastrowid
		except Exception:
			logger.exception(f"[trade_log] insert_entry 실패: {code}")
			return None


async def update_mae_mfe(code: str, current_price: float):
	"""0B push 시 호출. 미청산 trade의 mae/mfe 갱신.

	갱신형: current가 mae보다 낮으면 mae 갱신, mfe보다 높으면 mfe 갱신.
	동일 종목 미청산 trade가 여럿이면 모두 갱신 (UNIQUE(code, ord_no)지만
	이론적으로 분리 등록 가능).
	"""
	async with _lock:
		try:
			ts = datetime.now().isoformat(timespec='seconds')
			with _conn() as c:
				# 미청산 trade 조회
				rows = c.execute(
					"""SELECT id, entry_price, mae_price, mfe_price
					   FROM touch_trades
					   WHERE code = ? AND ts_exit IS NULL""",
					(code,),
				).fetchall()
				for r in rows:
					tid = r['id']
					entry = r['entry_price']
					mae = r['mae_price']
					mfe = r['mfe_price']
					if current_price < mae:
						pct = (current_price - entry) / entry * 100.0 if entry else 0.0
						c.execute(
							"UPDATE touch_trades SET mae_price=?, mae_ts=?, mae_pct=? WHERE id=?",
							(current_price, ts, pct, tid),
						)
					if current_price > mfe:
						pct = (current_price - entry) / entry * 100.0 if entry else 0.0
						c.execute(
							"UPDATE touch_trades SET mfe_price=?, mfe_ts=?, mfe_pct=? WHERE id=?",
							(current_price, ts, pct, tid),
						)
		except Exception:
			logger.exception(f"[trade_log] update_mae_mfe 실패: {code}")


async def update_exit(code: str, ord_no: str, exit_price: float, exit_reason: str):
	"""청산 시점에 호출. ts_exit / exit_price / exit_reason / pnl_won / pnl_pct 갱신.

	exit_reason: 'stop_loss' | 'take_profit' | 'closing_auction_1520' | 'manual'
	같은 code의 가장 오래된 미청산 trade를 갱신 (FIFO). ord_no 우선 매칭.
	"""
	async with _lock:
		try:
			ts = datetime.now().isoformat(timespec='seconds')
			with _conn() as c:
				# ord_no 우선 매칭, 없으면 FIFO 미청산
				row = c.execute(
					"""SELECT id, entry_price, qty FROM touch_trades
					   WHERE code = ? AND ord_no = ? AND ts_exit IS NULL
					   LIMIT 1""",
					(code, ord_no or ''),
				).fetchone()
				if row is None:
					row = c.execute(
						"""SELECT id, entry_price, qty FROM touch_trades
						   WHERE code = ? AND ts_exit IS NULL
						   ORDER BY ts_entry ASC LIMIT 1""",
						(code,),
					).fetchone()
				if row is None:
					logger.info(f"[trade_log] {code} 미청산 trade 없음 — update_exit 스킵")
					return
				tid = row['id']
				entry = row['entry_price']
				qty = row['qty']
				pnl_won = int(round((exit_price - entry) * qty)) if entry else 0
				pnl_pct = (exit_price - entry) / entry * 100.0 if entry else 0.0
				c.execute(
					"""UPDATE touch_trades
					   SET ts_exit=?, exit_price=?, exit_reason=?, pnl_won=?, pnl_pct=?
					   WHERE id=?""",
					(ts, exit_price, exit_reason, pnl_won, pnl_pct, tid),
				)
				logger.info(f"[trade_log] exit {code} reason={exit_reason} pnl={pnl_won:+,}원 ({pnl_pct:+.2f}%)")
		except Exception:
			logger.exception(f"[trade_log] update_exit 실패: {code}")


async def get_open_trade(code: str) -> Optional[dict]:
	"""미청산 trade 1건 조회 (FIFO). 분석/디버깅용."""
	async with _lock:
		try:
			with _conn() as c:
				row = c.execute(
					"""SELECT * FROM touch_trades
					   WHERE code = ? AND ts_exit IS NULL
					   ORDER BY ts_entry ASC LIMIT 1""",
					(code,),
				).fetchone()
				return dict(row) if row else None
		except Exception:
			logger.exception(f"[trade_log] get_open_trade 실패: {code}")
			return None
