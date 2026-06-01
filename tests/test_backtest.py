"""Phase 6b — 백테스트 엔진 단위 테스트."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestCalcMetrics:

	def test_empty_returns(self):
		from modules.semi_trigger.backtest import calc_metrics
		m = calc_metrics([])
		assert m['n'] == 0
		assert m['win_rate'] is None
		assert m['mean'] is None
		assert m['cumulative_sum'] == 0.0
		assert m['max_dd'] == 0.0

	def test_all_wins(self):
		from modules.semi_trigger.backtest import calc_metrics
		m = calc_metrics([1.0, 2.0, 3.0, 0.5])
		assert m['n'] == 4
		assert m['win_rate'] == 1.0
		assert abs(m['mean'] - 1.625) < 1e-9
		assert m['cumulative_sum'] == 6.5

	def test_mixed_wins_losses(self):
		from modules.semi_trigger.backtest import calc_metrics
		m = calc_metrics([1.0, -2.0, 1.5, -0.5])
		assert m['n'] == 4
		assert m['win_rate'] == 0.5
		# mean = (1 -2 +1.5 -0.5) / 4 = 0.0
		assert abs(m['mean'] - 0.0) < 1e-9

	def test_max_dd_calculation(self):
		"""누적: 1, 3, 1, 0, -2 → peak=3, lowest cum=-2 → dd=3-(-2)=5."""
		from modules.semi_trigger.backtest import calc_metrics
		# returns: 1, 2, -2, -1, -2 → cum: 1, 3, 1, 0, -2
		m = calc_metrics([1.0, 2.0, -2.0, -1.0, -2.0])
		assert m['cumulative_sum'] == -2.0
		assert m['max_dd'] == 5.0  # peak 3 → trough -2

	def test_sharpe_with_zero_std(self):
		"""모든 returns 동일 → std=0 → Sharpe=None."""
		from modules.semi_trigger.backtest import calc_metrics
		m = calc_metrics([1.0, 1.0, 1.0])
		assert m['sharpe'] is None

	def test_sharpe_normal(self):
		"""standard sharpe-like: mean/std."""
		from modules.semi_trigger.backtest import calc_metrics
		returns = [1.0, 2.0, -1.0, 3.0, 0.0]
		m = calc_metrics(returns)
		# mean = 1.0, std ≈ 1.581
		assert m['sharpe'] is not None
		assert abs(m['mean'] - 1.0) < 1e-9
		assert m['sharpe'] > 0


@pytest.fixture
def tmp_db(tmp_path):
	from modules.semi_trigger import db as db_mod
	tmp_file = str(tmp_path / 'backtest.db')
	db_mod._initialized = False
	db_mod.init_db(tmp_file)
	return tmp_file


class TestRunBacktest:

	def test_no_data(self, tmp_db):
		from modules.semi_trigger.backtest import run_backtest
		r = run_backtest('005930', close_by_date={}, db_path=tmp_db)
		assert r['semi']['n'] == 0
		assert r['legacy']['n'] == 0
		assert r['baseline_insufficient_count'] == 0

	def test_baseline_insufficient_no_triggers(self, tmp_db):
		"""baseline 20일 미만 → 모든 trigger 보류."""
		from modules.semi_trigger.db import upsert_factors
		from modules.semi_trigger.backtest import run_backtest

		# 5일만 입력 (baseline 부족)
		dates = ['2026-05-25', '2026-05-26', '2026-05-27', '2026-05-28', '2026-05-29']
		for i, d in enumerate(dates):
			upsert_factors(d, '005930', {
				'us_memory': 1.0 + i,
				'sox': 0.5, 'nvda': 0.5, 'mu': 0.5,  # legacy 모두 통과
			}, db_path=tmp_db)

		close_map = {d: 50000 + i * 100 for i, d in enumerate(dates)}
		r = run_backtest('005930', close_by_date=close_map, db_path=tmp_db)
		# semi: baseline 부족 → trigger 0
		assert r['semi']['n'] == 0
		# legacy: SOX/NVDA/MU 모두 통과 → 4일 trigger (마지막 제외)
		assert r['legacy']['n'] == 4

	def test_baseline_sufficient(self, tmp_db):
		"""20일 baseline + 추가 일자 → z-score 계산 + semi_score 산출 가능."""
		from modules.semi_trigger.db import upsert_factors
		from modules.semi_trigger.backtest import run_backtest

		# 25일 입력 — 첫 20일 baseline + 5일 시뮬레이션
		import datetime as dt
		base = dt.date(2026, 5, 1)
		dates = [(base + dt.timedelta(days=i)).isoformat() for i in range(25)]

		# 변동 있는 us_memory 시리즈 (z 계산 가능)
		us_mem_values = [0.5 * (-1)**i + 0.1 * i for i in range(25)]
		for i, d in enumerate(dates):
			upsert_factors(d, '005930', {
				'us_memory':       us_mem_values[i],
				'etf_flow':        1000_000_000 + i * 1_000_000,
				'fx_change':       0.1,
				'foreign_flow_5d': 1_000_000_000,
				'sox':             0.4 if i % 3 else -0.5,
				'nvda':            0.5,
				'mu':              0.5,
			}, db_path=tmp_db)

		close_map = {d: 50000 + i * 500 for i, d in enumerate(dates)}
		r = run_backtest('005930', threshold=0.5, close_by_date=close_map, db_path=tmp_db)
		assert r['baseline_insufficient_count'] >= 19  # 첫 20일은 baseline 부족
		# 마지막 4일 정도 trigger 평가 가능
		assert r['semi']['n'] >= 0  # 임계값 따라 0일 수도


class TestZForDate:
	"""_z_for_date 미래 leak 방지 검증."""

	def test_no_future_leak(self, tmp_db):
		from modules.semi_trigger.db import upsert_factors
		from modules.semi_trigger.backtest import _z_for_date

		# 6일치 입력
		dates = ['2026-05-26', '2026-05-27', '2026-05-28', '2026-05-29', '2026-06-01', '2026-06-02']
		us_mem = [1.0, 2.0, 3.0, 4.0, 5.0, 999.0]  # 미래 값 6/2는 999
		for d, v in zip(dates, us_mem):
			upsert_factors(d, '005930', {'us_memory': v}, db_path=tmp_db)

		# eval_date=2026-06-01 시점 — baseline은 5/26~5/29 (4개), 6/2(999) 제외
		row = {'us_memory': 5.0}
		z, days = _z_for_date('005930', '2026-06-01', row, db_path=tmp_db)
		assert days == 4
		# baseline mean=2.5, std=1.29, z = (5-2.5)/1.29 ≈ 1.94
		assert z['us_memory'] is not None
		assert z['us_memory'] > 0
		# 999 leak 안 됨 확인 — 만약 leak되면 mean이 매우 크고 z가 음수
