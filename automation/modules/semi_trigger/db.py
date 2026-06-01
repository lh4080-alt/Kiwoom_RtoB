"""semi_trigger SQLite — 일별 5축 factors + scores 누적.

spec v3 §6.1:
  daily_factors: 일자×종목별 raw 5축 값
  scores:        z-score + semi_score + trigger 결과

20일 z-score baseline 계산용. JSON으로는 비효율적.
경로: config/data/semi_trigger.db (atomic file ops 불필요 — SQLite 자체 WAL).
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_DB_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'semi_trigger.db')

_lock = threading.Lock()
_initialized = False


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_factors (
	date TEXT NOT NULL,
	stock_code TEXT NOT NULL,
	us_memory REAL,
	etf_flow REAL,
	fx_change REAL,
	foreign_flow_5d REAL,
	memory_price REAL,
	nasdaq_futures REAL,
	sox REAL,
	nvda REAL,
	mu REAL,
	PRIMARY KEY (date, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_daily_factors_code_date
	ON daily_factors (stock_code, date DESC);

CREATE TABLE IF NOT EXISTS scores (
	date TEXT NOT NULL,
	stock_code TEXT NOT NULL,
	us_memory_z REAL,
	etf_flow_z REAL,
	fx_z REAL,
	foreign_flow_z REAL,
	memory_price_z REAL,
	semi_score REAL,
	trigger INTEGER,
	legacy_trigger INTEGER,
	baseline_days INTEGER,
	weight_redistributed INTEGER,
	PRIMARY KEY (date, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_scores_code_date
	ON scores (stock_code, date DESC);
"""


def get_db_path() -> str:
	"""DB 경로 — 외부 테스트가 monkeypatch로 override 가능."""
	return _DB_PATH


def init_db(db_path: Optional[str] = None) -> None:
	"""DB 파일 + 스키마 초기화. idempotent (CREATE IF NOT EXISTS).

	기존 DB에 nasdaq_futures 컬럼 없으면 ALTER TABLE로 추가 (호환성).
	"""
	global _initialized
	path = db_path or get_db_path()
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with sqlite3.connect(path) as conn:
		conn.executescript(SCHEMA_SQL)
		# 기존 DB 마이그레이션 — nasdaq_futures 컬럼 부재 시 추가
		cur = conn.execute("PRAGMA table_info(daily_factors)")
		cols = {row[1] for row in cur.fetchall()}
		if 'nasdaq_futures' not in cols:
			conn.execute("ALTER TABLE daily_factors ADD COLUMN nasdaq_futures REAL")
		conn.commit()
	_initialized = True


@contextmanager
def connect(db_path: Optional[str] = None):
	"""sqlite3.Connection 컨텍스트 매니저. row_factory=Row.

	with connect() as conn:
	    cur = conn.execute(...)
	"""
	path = db_path or get_db_path()
	if not _initialized:
		init_db(path)
	conn = sqlite3.connect(path)
	conn.row_factory = sqlite3.Row
	try:
		yield conn
	finally:
		conn.close()


def upsert_factors(date: str, stock_code: str, factors: dict,
                   db_path: Optional[str] = None) -> None:
	"""daily_factors 단건 INSERT OR REPLACE (부분 upsert 지원).

	factors keys: us_memory, etf_flow, fx_change, foreign_flow_5d,
	              memory_price, nasdaq_futures, sox, nvda, mu (모두 optional, None 허용)

	특정 키만 전달하면 나머지는 기존 값 유지 (16:00 evening 부분 저장 후
	08:30 morning 보완 패턴 지원).
	"""
	all_cols = ('us_memory', 'etf_flow', 'fx_change', 'foreign_flow_5d',
	            'memory_price', 'nasdaq_futures', 'sox', 'nvda', 'mu')
	with connect(db_path) as conn:
		# 기존 row 조회 (부분 upsert)
		cur = conn.execute(
			"SELECT * FROM daily_factors WHERE date = ? AND stock_code = ?",
			(date, stock_code),
		)
		row = cur.fetchone()
		merged = {}
		for c in all_cols:
			if c in factors:
				merged[c] = factors[c]
			elif row is not None:
				merged[c] = row[c] if c in row.keys() else None
			else:
				merged[c] = None
		values = [merged[c] for c in all_cols]
		conn.execute(
			"INSERT OR REPLACE INTO daily_factors "
			"(date, stock_code, us_memory, etf_flow, fx_change, foreign_flow_5d, "
			" memory_price, nasdaq_futures, sox, nvda, mu) "
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
			(date, stock_code, *values),
		)
		conn.commit()


def upsert_score(date: str, stock_code: str, score_row: dict,
                 db_path: Optional[str] = None) -> None:
	"""scores 단건 INSERT OR REPLACE."""
	cols = ('us_memory_z', 'etf_flow_z', 'fx_z', 'foreign_flow_z',
	        'memory_price_z', 'semi_score', 'trigger', 'legacy_trigger',
	        'baseline_days', 'weight_redistributed')
	values = [score_row.get(c) for c in cols]
	with connect(db_path) as conn:
		conn.execute(
			"INSERT OR REPLACE INTO scores "
			"(date, stock_code, us_memory_z, etf_flow_z, fx_z, foreign_flow_z, "
			" memory_price_z, semi_score, trigger, legacy_trigger, baseline_days, "
			" weight_redistributed) "
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
			(date, stock_code, *values),
		)
		conn.commit()


def fetch_recent_factors(stock_code: str, n: int = 20,
                         before_date: Optional[str] = None,
                         db_path: Optional[str] = None) -> list:
	"""최근 N일 factors 조회 (date DESC). z-score baseline 계산용.

	Args:
		before_date: 지정하면 해당 일자보다 과거(<)만 반환 (walk-forward 백테스트용).
		             None이면 전체 최근 N일.
	"""
	with connect(db_path) as conn:
		if before_date:
			cur = conn.execute(
				"SELECT * FROM daily_factors WHERE stock_code = ? AND date < ? "
				"ORDER BY date DESC LIMIT ?",
				(stock_code, before_date, n),
			)
		else:
			cur = conn.execute(
				"SELECT * FROM daily_factors WHERE stock_code = ? "
				"ORDER BY date DESC LIMIT ?",
				(stock_code, n),
			)
		return [dict(r) for r in cur.fetchall()]


def fetch_score(date: str, stock_code: str,
                db_path: Optional[str] = None) -> Optional[dict]:
	"""특정 일자 score 조회. 없으면 None."""
	with connect(db_path) as conn:
		cur = conn.execute(
			"SELECT * FROM scores WHERE date = ? AND stock_code = ?",
			(date, stock_code),
		)
		row = cur.fetchone()
		return dict(row) if row else None
