"""엣지 분석기 헬퍼 단위 테스트 (Lee 5/27 지시서 4대 방향).

검증:
  - calc_price_location: 종가 위치 % (분모 0/cur 0/정상 case)
  - qty_thousand_to_eok: 수량(천주) × 종가 → 억원 정규화
  - program_amount_mm_to_eok: 백만원 → 억원
  - calculate_edge_score: 4대 가중치 매트릭스
"""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestCalcPriceLocation:

	def test_close_at_high(self):
		"""종가=고가 → 100%."""
		from modules.daily_analyzer import calc_price_location
		assert calc_price_location(300000, 300000, 290000) == 100.0

	def test_close_at_low(self):
		"""종가=저가 → 0%."""
		from modules.daily_analyzer import calc_price_location
		assert calc_price_location(290000, 300000, 290000) == 0.0

	def test_close_mid(self):
		"""종가 중간 → 50%."""
		from modules.daily_analyzer import calc_price_location
		assert calc_price_location(295000, 300000, 290000) == 50.0

	def test_high_eq_low_returns_default(self):
		"""분모 0 → 50 안전 기본값."""
		from modules.daily_analyzer import calc_price_location
		assert calc_price_location(100, 100, 100) == 50.0

	def test_zero_close_returns_default(self):
		"""종가 0 → 50 안전 기본값."""
		from modules.daily_analyzer import calc_price_location
		assert calc_price_location(0, 300000, 290000) == 50.0


class TestQtyThousandToEok:
	"""ka10059 수량(천주) → 억원 정규화."""

	def test_normal_positive(self):
		"""삼성전자 외인 +100천주 × 295,000원 = 295억."""
		from modules.daily_analyzer import qty_thousand_to_eok
		result = qty_thousand_to_eok(100, 295000)
		assert result == 295.0

	def test_negative_value(self):
		"""음수 매도 케이스."""
		from modules.daily_analyzer import qty_thousand_to_eok
		result = qty_thousand_to_eok(-128, 295000)
		assert result == -377.6

	def test_zero_close_returns_zero(self):
		"""close=0 안전망."""
		from modules.daily_analyzer import qty_thousand_to_eok
		assert qty_thousand_to_eok(100, 0) == 0.0


class TestProgramAmountMmToEok:

	def test_positive_million_to_eok(self):
		"""500백만원 = 5억."""
		from modules.daily_analyzer import program_amount_mm_to_eok
		assert program_amount_mm_to_eok(500) == 5.0

	def test_negative(self):
		"""음수: -2134262백만 = -21342.62억."""
		from modules.daily_analyzer import program_amount_mm_to_eok
		assert program_amount_mm_to_eok(-2134262) == -21342.62


class TestCalculateEdgeScore:
	"""4대 가중치 매트릭스 — 점수 합산 검증."""

	def test_baseline_50(self):
		"""중립값 — 베이스 50점 유지."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=60, smart_money_eok=2.0,
			trust_days=1, pension_days=1,
			cntr_str_5min=100, program_eok=0,
		)
		assert s == 50

	def test_perfect_storm(self):
		"""모든 조건 만족 — 50+15+15+10+10 = 100."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=95, smart_money_eok=30.0,
			trust_days=5, pension_days=5,
			cntr_str_5min=150, program_eok=10.0,
		)
		assert s == 100

	def test_bull_trap_penalty(self):
		"""종가 위치 50% 미만 → -25점."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=30, smart_money_eok=2.0,
			trust_days=1, pension_days=1,
			cntr_str_5min=100, program_eok=0,
		)
		assert s == 25  # 50 - 25

	def test_heavy_program_sell(self):
		"""프로그램 -10억 미만 → -15점."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=60, smart_money_eok=2.0,
			trust_days=1, pension_days=1,
			cntr_str_5min=100, program_eok=-15.0,
		)
		assert s == 35  # 50 - 15

	def test_smart_money_mid(self):
		"""투신+연기금 5억 ≤ x < 20억 → +10."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=60, smart_money_eok=10.0,
			trust_days=1, pension_days=1,
			cntr_str_5min=100, program_eok=0,
		)
		assert s == 60  # 50 + 10

	def test_smart_money_high(self):
		"""투신+연기금 20억↑ → +15."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=60, smart_money_eok=25.0,
			trust_days=1, pension_days=1,
			cntr_str_5min=100, program_eok=0,
		)
		assert s == 65  # 50 + 15

	def test_continuity_trust_3days(self):
		"""투신 3일 연속 → +10."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=60, smart_money_eok=2.0,
			trust_days=3, pension_days=1,
			cntr_str_5min=100, program_eok=0,
		)
		assert s == 60  # 50 + 10

	def test_matrix_synergy(self):
		"""체결강도 120+ AND 프로그램 5억+ → +10."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=60, smart_money_eok=2.0,
			trust_days=1, pension_days=1,
			cntr_str_5min=125, program_eok=8.0,
		)
		assert s == 60  # 50 + 10

	def test_clamp_min_zero(self):
		"""감점이 누적되어 음수가 되면 0으로 clamp."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=30, smart_money_eok=2.0,
			trust_days=1, pension_days=1,
			cntr_str_5min=100, program_eok=-20.0,
		)
		# 50 - 25 - 15 = 10 (clamp 안 됨)
		assert s == 10

	def test_clamp_max_100(self):
		"""모든 가산 최대 — 100 clamp."""
		from modules.daily_analyzer import calculate_edge_score
		s = calculate_edge_score(
			price_location=100, smart_money_eok=100.0,
			trust_days=10, pension_days=10,
			cntr_str_5min=200, program_eok=100.0,
		)
		assert s == 100
