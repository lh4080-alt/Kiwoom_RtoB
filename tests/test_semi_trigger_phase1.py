"""semi_trigger Phase 1 — ① 미 메모리 + scoring 헬퍼 단위 테스트."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestCalcUsMemory:
	"""① 4종목 동일가중 평균 (None 제외)."""

	def test_all_present(self):
		from modules.semi_trigger.collectors.us_memory import calc_us_memory
		r = calc_us_memory({'MU': 1.0, 'WDC': 2.0, 'SNDK': 3.0, 'STX': 4.0})
		assert r == 2.5  # (1+2+3+4)/4

	def test_partial_none_excluded(self):
		"""None 종목은 평균에서 제외."""
		from modules.semi_trigger.collectors.us_memory import calc_us_memory
		r = calc_us_memory({'MU': 1.0, 'WDC': None, 'SNDK': 3.0, 'STX': None})
		assert r == 2.0  # (1+3)/2

	def test_all_none(self):
		from modules.semi_trigger.collectors.us_memory import calc_us_memory
		r = calc_us_memory({'MU': None, 'WDC': None, 'SNDK': None, 'STX': None})
		assert r is None

	def test_empty_dict(self):
		from modules.semi_trigger.collectors.us_memory import calc_us_memory
		assert calc_us_memory({}) is None

	def test_negative_values(self):
		from modules.semi_trigger.collectors.us_memory import calc_us_memory
		r = calc_us_memory({'MU': -1.0, 'WDC': -2.0, 'SNDK': 1.0, 'STX': 2.0})
		assert r == 0.0


class TestCalcZscore:

	def test_normal_zscore(self):
		from modules.semi_trigger.scoring import calc_zscore
		# baseline mean=2, sample std=1.0 (n-1=2 → sqrt(2/2)=1) → z=(4-2)/1=2.0
		baseline = [1.0, 2.0, 3.0]
		current = 4.0
		z = calc_zscore(baseline, current)
		assert abs(z - 2.0) < 0.01

	def test_baseline_too_short(self):
		from modules.semi_trigger.scoring import calc_zscore
		assert calc_zscore([1.0], 2.0) is None
		assert calc_zscore([], 2.0) is None

	def test_zero_std(self):
		"""baseline 전부 같은 값 → std=0 → None."""
		from modules.semi_trigger.scoring import calc_zscore
		assert calc_zscore([5.0, 5.0, 5.0], 5.0) is None

	def test_none_current(self):
		from modules.semi_trigger.scoring import calc_zscore
		assert calc_zscore([1.0, 2.0, 3.0], None) is None

	def test_none_in_baseline_excluded(self):
		"""baseline 내 None은 제외하고 계산."""
		from modules.semi_trigger.scoring import calc_zscore
		z = calc_zscore([1.0, None, 2.0, None, 3.0], 4.0)
		# 유효 baseline = [1,2,3] (3개) — 위 test_normal_zscore와 동일
		assert abs(z - 2.0) < 0.01


class TestCalcSemiScore:

	def test_all_axes_present(self):
		from modules.semi_trigger.scoring import calc_semi_score, WEIGHTS
		z_values = {
			'us_memory': 2.0,    # 0.40
			'etf_flow': 1.5,     # 0.20
			'fx': 0.5,           # 0.20
			'foreign_flow': 1.0, # 0.10
			'memory_price': 0.3, # 0.10
		}
		expected = (0.40 * 2.0 + 0.20 * 1.5 + 0.20 * 0.5 + 0.10 * 1.0 + 0.10 * 0.3)
		r = calc_semi_score(z_values)
		assert abs(r['semi_score'] - expected) < 1e-9
		assert r['weight_redistributed'] is False
		assert len(r['used_axes']) == 5

	def test_all_none(self):
		from modules.semi_trigger.scoring import calc_semi_score
		r = calc_semi_score({k: None for k in
		                     ('us_memory', 'etf_flow', 'fx', 'foreign_flow', 'memory_price')})
		assert r['semi_score'] is None
		assert r['weight_redistributed'] is False
		assert r['used_axes'] == []

	def test_redistribution_when_missing_axis(self):
		"""memory_price (10%) 결측 → 나머지 90% 비례 재분배."""
		from modules.semi_trigger.scoring import calc_semi_score
		z_values = {
			'us_memory': 1.0,    # 0.40 / 0.90 = 0.4444
			'etf_flow': 1.0,     # 0.20 / 0.90 = 0.2222
			'fx': 1.0,           # 0.20 / 0.90 = 0.2222
			'foreign_flow': 1.0, # 0.10 / 0.90 = 0.1111
			'memory_price': None,
		}
		r = calc_semi_score(z_values)
		# 모든 z가 1.0이면 가중 정규화 후에도 1.0
		assert abs(r['semi_score'] - 1.0) < 1e-9
		assert r['weight_redistributed'] is True
		assert 'memory_price' not in r['used_axes']
		assert len(r['used_axes']) == 4

	def test_only_us_memory(self):
		"""us_memory만 유효 → 다른 4축 결측 → us_memory에 100% 가중."""
		from modules.semi_trigger.scoring import calc_semi_score
		r = calc_semi_score({
			'us_memory': 2.5,
			'etf_flow': None, 'fx': None,
			'foreign_flow': None, 'memory_price': None,
		})
		assert r['semi_score'] == 2.5  # 0.40/0.40 = 1.0
		assert r['weight_redistributed'] is True
		assert r['used_axes'] == ['us_memory']

	def test_weights_sum_to_1(self):
		"""가중치 합 = 1.0 (확정값 검증)."""
		from modules.semi_trigger.scoring import WEIGHTS
		assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


class TestBaselineSufficient:

	def test_threshold_20(self):
		from modules.semi_trigger.scoring import is_baseline_sufficient
		assert is_baseline_sufficient(20) is True
		assert is_baseline_sufficient(19) is False
		assert is_baseline_sufficient(0) is False
		assert is_baseline_sufficient(50) is True
