"""semi_trigger walk-forward 백테스트 엔진 — Phase 6b.

각 일자 d (eval):
  1. baseline = DB의 [d 이전 20일] daily_factors
  2. 오늘 raw factors = DB의 [d] daily_factors
  3. z-score + semi_score + trigger 산출 (walk-forward, 미래 leak 없음)
  4. 익일 d+1 수익률 (close[d+1]/close[d] - 1) 기록
  5. legacy_trigger도 병행

Metrics:
  - count_triggered: 트리거 발동 일수
  - win_rate: 익일 수익률 > 0 비율
  - mean_return: 평균 익일 수익률 (%)
  - cumulative_return: 단순 합 (복리 X — sanity 표시)
  - max_dd: 누적 수익률 max drawdown
  - sharpe: mean / std (연환산 안 함)

영구 원칙 #30: 봇 데몬 내부에서만 실행.
"""
import logging
import math
import statistics
from typing import Optional

from . import db as st_db
from .scoring import (
	BASELINE_MIN_DAYS, calc_zscore, calc_semi_score, calc_legacy_trigger,
)
from .pipeline import AXIS_TO_DB_COL

logger = logging.getLogger(__name__)


def _z_for_date(stock_code: str, eval_date: str, raw_row: dict,
                db_path: Optional[str] = None) -> tuple:
	"""특정 일자에 대해 z-score dict + baseline_days.

	baseline: eval_date 이전 BASELINE_MIN_DAYS+1 일치에서 eval_date 제외.
	"""
	# 미래 leak 방지 — before_date=eval_date 사용 (그날 자신 제외)
	baseline_rows = st_db.fetch_recent_factors(
		stock_code, n=BASELINE_MIN_DAYS + 5, before_date=eval_date, db_path=db_path,
	)
	baseline_days = len(baseline_rows)

	current_map = {
		'us_memory':       raw_row.get('us_memory'),
		'price_change':    raw_row.get('price_change'),
		'volume_amount':   raw_row.get('volume_amount'),
		'volume_ratio':    raw_row.get('volume_ratio'),
		'program_net':     raw_row.get('program_net'),
		'fx':              raw_row.get('fx_change'),
		'foreign_flow':    raw_row.get('foreign_flow_5d'),
		'nasdaq_futures':  raw_row.get('nasdaq_futures'),
	}
	z = {}
	for axis, cur in current_map.items():
		baseline_vals = [r.get(AXIS_TO_DB_COL[axis]) for r in baseline_rows]
		z[axis] = calc_zscore(baseline_vals, cur)
	return z, baseline_days


def calc_metrics(returns: list) -> dict:
	"""익일 수익률 리스트 → 지표.

	Args:
		returns: 트리거 발동 일자의 익일 수익률 (%, float)

	Returns: {
	  'n': count, 'win_rate', 'mean', 'std', 'sharpe',
	  'cumulative_sum', 'max_dd'
	}
	"""
	n = len(returns)
	if n == 0:
		return {'n': 0, 'win_rate': None, 'mean': None, 'std': None,
		        'sharpe': None, 'cumulative_sum': 0.0, 'max_dd': 0.0}

	wins = sum(1 for r in returns if r > 0)
	win_rate = wins / n
	mean = statistics.mean(returns)
	std = statistics.stdev(returns) if n >= 2 else 0.0
	sharpe = (mean / std) if std > 0 else None

	# 누적 합 + max drawdown (수익률 단순 합 기준)
	cum = 0.0
	peak = 0.0
	max_dd = 0.0
	for r in returns:
		cum += r
		if cum > peak:
			peak = cum
		dd = peak - cum  # 양수 = drawdown 크기
		if dd > max_dd:
			max_dd = dd

	return {
		'n':               n,
		'win_rate':        win_rate,
		'mean':            mean,
		'std':             std,
		'sharpe':          sharpe,
		'cumulative_sum':  cum,
		'max_dd':          max_dd,
	}


def run_backtest(stock_code: str, close_by_date: dict, threshold: float = 1.0,
                 start_date: Optional[str] = None, end_date: Optional[str] = None,
                 db_path: Optional[str] = None) -> dict:
	"""walk-forward 백테스트 (단일 종목).

	Args:
		stock_code: 005930 / 000660
		close_by_date: {YYYY-MM-DD: close(원)} — 익일 수익률 계산용
		threshold: trigger 임계값
		start_date / end_date: ISO 일자 범위 (None=DB 전체)

	Returns: {
	  'stock_code', 'threshold', 'window': (start, end),
	  'semi':  {'n', 'win_rate', 'mean', 'std', 'sharpe', 'cumulative_sum', 'max_dd'},
	  'legacy': {동일 구조},
	  'baseline_insufficient_count': int,
	}
	"""
	all_rows = st_db.fetch_recent_factors(stock_code, n=10000, db_path=db_path)
	# date ASC로 정렬
	all_rows = sorted(all_rows, key=lambda r: r.get('date', ''))

	if start_date:
		all_rows = [r for r in all_rows if r.get('date', '') >= start_date]
	if end_date:
		all_rows = [r for r in all_rows if r.get('date', '') <= end_date]

	if not all_rows:
		return {'stock_code': stock_code, 'threshold': threshold,
		        'semi': calc_metrics([]), 'legacy': calc_metrics([]),
		        'baseline_insufficient_count': 0,
		        'window': (None, None)}

	# 영업일 정렬 list of dates
	dates_sorted = [r['date'] for r in all_rows]

	semi_returns = []
	legacy_returns = []
	baseline_insufficient = 0

	for i, row in enumerate(all_rows):
		eval_date = row['date']
		# 익일 close 필요 — dates_sorted[i+1]
		if i + 1 >= len(all_rows):
			break  # 마지막 일자는 익일 없음
		next_date = dates_sorted[i + 1]
		cur_close = close_by_date.get(eval_date)
		nxt_close = close_by_date.get(next_date)
		if not cur_close or not nxt_close or cur_close <= 0:
			continue
		next_return = (nxt_close - cur_close) / cur_close * 100.0

		# legacy_trigger — baseline 무관, raw값만 사용
		legacy_t = calc_legacy_trigger(
			row.get('sox'), row.get('nvda'), row.get('mu'),
		)
		if legacy_t == 1:
			legacy_returns.append(next_return)

		# semi_score — baseline 20일 필요
		z, baseline_days = _z_for_date(stock_code, eval_date, row, db_path=db_path)
		if baseline_days < BASELINE_MIN_DAYS:
			baseline_insufficient += 1
			continue
		score_result = calc_semi_score(z)
		semi_score = score_result['semi_score']
		if semi_score is not None and semi_score >= threshold:
			semi_returns.append(next_return)

	semi_metrics = calc_metrics(semi_returns)
	legacy_metrics = calc_metrics(legacy_returns)

	return {
		'stock_code':                  stock_code,
		'threshold':                   threshold,
		'window':                      (dates_sorted[0], dates_sorted[-1]),
		'semi':                        semi_metrics,
		'legacy':                      legacy_metrics,
		'baseline_insufficient_count': baseline_insufficient,
		'total_days':                  len(all_rows),
	}
