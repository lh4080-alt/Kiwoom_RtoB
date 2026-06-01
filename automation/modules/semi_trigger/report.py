"""semi_trigger Phase 6c — IS/OOS 분리 + 임계값 튜닝 + semi vs legacy 비교 리포트.

walk-forward 백테스트를 IS(앞 70%) / OOS(뒤 30%)로 분할:
  1. IS에서 임계값 그리드 (0.5/0.6/...) 평가
  2. IS best 임계값 선택 (Sharpe 또는 mean × n 기준)
  3. OOS에서 best 임계값으로 검증
  4. semi vs legacy 비교 → shadow 통과 여부 판단

영구 원칙 #30: 봇 데몬 내부에서만 실행.
"""
import logging
from typing import Optional

from . import db as st_db
from .backtest import run_backtest

logger = logging.getLogger(__name__)


def split_window(dates_sorted: list, ratio: float = 0.7) -> tuple:
	"""정렬된 일자 리스트를 IS/OOS로 분할.

	Args:
		dates_sorted: ASC 일자 리스트
		ratio: IS 비율 (기본 0.7)

	Returns: (is_dates, oos_dates)
	"""
	n = len(dates_sorted)
	split = int(n * ratio)
	return dates_sorted[:split], dates_sorted[split:]


def pick_best_threshold(grid_results: list, criterion: str = 'sharpe',
                        min_n: int = 5) -> Optional[dict]:
	"""IS 그리드 결과 → 최적 임계값.

	Args:
		grid_results: [{'threshold', 'semi': {n, sharpe, mean, ...}}, ...]
		criterion: 'sharpe' / 'mean' / 'mean_x_n'
		min_n: 최소 trigger 발동 수 (이하 제외 — sample size 보장)

	Returns: best row (dict) 또는 None.
	"""
	eligible = [r for r in grid_results if r['semi']['n'] >= min_n]
	if not eligible:
		return None

	if criterion == 'sharpe':
		key_fn = lambda r: (r['semi']['sharpe'] or float('-inf'))
	elif criterion == 'mean':
		key_fn = lambda r: (r['semi']['mean'] or float('-inf'))
	elif criterion == 'mean_x_n':
		key_fn = lambda r: ((r['semi']['mean'] or 0) * r['semi']['n'])
	else:
		raise ValueError(f"unknown criterion: {criterion}")

	return max(eligible, key=key_fn)


def run_is_oos_report(stock_code: str, close_by_date: dict,
                     thresholds: list = None,
                     is_ratio: float = 0.7,
                     criterion: str = 'sharpe',
                     min_n: int = 5,
                     db_path: Optional[str] = None) -> dict:
	"""IS 그리드 튜닝 + OOS 검증 + legacy 비교.

	Args:
		stock_code: 005930 / 000660
		close_by_date: 익일 수익률용 close 매핑
		thresholds: 그리드 (None=기본 [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0])
		is_ratio: IS 비율
		criterion: 'sharpe'/'mean'/'mean_x_n'
		min_n: trigger 최소 발동 수

	Returns: 리포트 dict
	"""
	if thresholds is None:
		thresholds = [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0]

	# 전체 일자 (ASC)
	all_rows = sorted(
		st_db.fetch_recent_factors(stock_code, n=10000, db_path=db_path),
		key=lambda r: r.get('date', ''),
	)
	if not all_rows:
		return {'error': 'no data', 'stock_code': stock_code}

	dates = [r['date'] for r in all_rows]
	is_dates, oos_dates = split_window(dates, ratio=is_ratio)
	if not is_dates or not oos_dates:
		return {'error': 'insufficient data for split', 'stock_code': stock_code,
		        'total_days': len(dates)}

	is_start, is_end = is_dates[0], is_dates[-1]
	oos_start, oos_end = oos_dates[0], oos_dates[-1]

	# IS 그리드
	is_grid = []
	for t in thresholds:
		r = run_backtest(stock_code, close_by_date, threshold=t,
		                 start_date=is_start, end_date=is_end, db_path=db_path)
		is_grid.append({
			'threshold': t,
			'semi':      r['semi'],
			'legacy':    r['legacy'],
		})

	# best 선택
	best = pick_best_threshold(is_grid, criterion=criterion, min_n=min_n)

	# OOS 검증
	if best is not None:
		oos = run_backtest(stock_code, close_by_date, threshold=best['threshold'],
		                   start_date=oos_start, end_date=oos_end, db_path=db_path)
		oos_semi = oos['semi']
		oos_legacy = oos['legacy']
	else:
		oos_semi = None
		oos_legacy = None
		oos = run_backtest(stock_code, close_by_date, threshold=thresholds[0],
		                   start_date=oos_start, end_date=oos_end, db_path=db_path)
		oos_legacy = oos['legacy']

	# OOS shadow 통과 판단 — semi가 legacy보다 평균 수익률 + 승률 모두 우월
	verdict = _decide_verdict(oos_semi, oos_legacy)

	return {
		'stock_code':        stock_code,
		'total_days':        len(dates),
		'is_window':         (is_start, is_end, len(is_dates)),
		'oos_window':        (oos_start, oos_end, len(oos_dates)),
		'criterion':         criterion,
		'min_n':             min_n,
		'is_grid':           is_grid,
		'best_threshold':    best['threshold'] if best else None,
		'best_is_semi':      best['semi'] if best else None,
		'oos_semi':          oos_semi,
		'oos_legacy':        oos_legacy,
		'verdict':           verdict,
	}


def _decide_verdict(oos_semi: Optional[dict], oos_legacy: dict) -> str:
	"""OOS semi vs legacy 비교 → shadow 통과 판단.

	semi 우위 조건 (스펙 §7 Phase 6):
	  - semi n >= 5 (sample size)
	  - semi mean > legacy mean (평균 수익률 우위)
	  - semi win_rate >= legacy win_rate * 0.95 (승률 비슷 이상)
	  - semi max_dd <= legacy max_dd * 1.2 (MDD 크게 안 나쁨)

	Returns: 'pass_semi_better' / 'pass_close' / 'fail_semi_worse' / 'insufficient_data'
	"""
	if not oos_semi or oos_semi['n'] < 5:
		return 'insufficient_data'
	s_mean = oos_semi.get('mean')
	l_mean = oos_legacy.get('mean')
	if s_mean is None or l_mean is None:
		return 'insufficient_data'

	s_win = oos_semi.get('win_rate') or 0
	l_win = oos_legacy.get('win_rate') or 0
	s_mdd = oos_semi.get('max_dd') or 0
	l_mdd = oos_legacy.get('max_dd') or 0

	mean_ok = s_mean > l_mean
	win_ok = s_win >= l_win * 0.95
	mdd_ok = s_mdd <= l_mdd * 1.2

	if mean_ok and win_ok and mdd_ok:
		return 'pass_semi_better'
	if mean_ok and (win_ok or mdd_ok):
		return 'pass_close'
	return 'fail_semi_worse'


def format_report(report: dict) -> str:
	"""리포트 → 텍스트 (텔레그램/콘솔용)."""
	if 'error' in report:
		return f"❌ [{report['stock_code']}] {report['error']}"

	lines = [
		f"📊 [{report['stock_code']}] IS/OOS 백테스트 리포트",
		f"전체 {report['total_days']}일 / IS {report['is_window'][2]}일 / OOS {report['oos_window'][2]}일",
		f"IS: {report['is_window'][0]} ~ {report['is_window'][1]}",
		f"OOS: {report['oos_window'][0]} ~ {report['oos_window'][1]}",
		"",
		"📋 IS 그리드 (criterion=" + report['criterion'] + ")",
	]
	for r in report['is_grid']:
		s = r['semi']
		lines.append(
			f"  thr={r['threshold']:.2f}  n={s['n']:3d}  "
			f"win={s['win_rate']*100 if s['win_rate'] else 0:5.1f}%  "
			f"mean={(s['mean'] or 0):+5.2f}%  cum={s['cumulative_sum']:+6.1f}%  "
			f"sharpe={(s['sharpe'] or 0):5.2f}"
		)

	if report['best_threshold'] is not None:
		lines.extend([
			"",
			f"🎯 IS best threshold: {report['best_threshold']}",
		])
		oos_s = report['oos_semi'] or {}
		oos_l = report['oos_legacy']
		lines.extend([
			"",
			"🔍 OOS 검증",
			f"  SEMI:   n={oos_s.get('n', 0):3d}  win={(oos_s.get('win_rate') or 0)*100:5.1f}%  "
			f"mean={(oos_s.get('mean') or 0):+5.2f}%  cum={oos_s.get('cumulative_sum', 0):+6.1f}%  "
			f"mdd={oos_s.get('max_dd', 0):5.2f}",
			f"  LEGACY: n={oos_l['n']:3d}  win={(oos_l.get('win_rate') or 0)*100:5.1f}%  "
			f"mean={(oos_l.get('mean') or 0):+5.2f}%  cum={oos_l.get('cumulative_sum', 0):+6.1f}%  "
			f"mdd={oos_l.get('max_dd', 0):5.2f}",
		])
		lines.append("")
		lines.append(f"⚖️ 판정: {report['verdict']}")
	else:
		lines.append("\n⚠️ IS에서 min_n 통과한 임계값 없음")
	return "\n".join(lines)
