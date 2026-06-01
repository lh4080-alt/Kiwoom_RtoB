"""Phase 6a — 백필 순수 헬퍼 단위 테스트."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestToIsoDate:

	def test_normal(self):
		from modules.semi_trigger.backfill import to_iso_date
		assert to_iso_date('20260601') == '2026-06-01'

	def test_short_input(self):
		from modules.semi_trigger.backfill import to_iso_date
		assert to_iso_date('') == ''
		assert to_iso_date('2026') == ''


class TestAggregateForeign5dHistory:

	def test_normal_window(self):
		"""5일 window 외인 합산 × 1000 × close."""
		from modules.semi_trigger.backfill import aggregate_foreign_5d_history
		daily = [
			{'date': '20260601', 'frgnr_invsr': 100},
			{'date': '20260531', 'frgnr_invsr': -50},
			{'date': '20260530', 'frgnr_invsr': 200},
			{'date': '20260529', 'frgnr_invsr': 50},
			{'date': '20260528', 'frgnr_invsr': 30},
			{'date': '20260527', 'frgnr_invsr': 10},
		]
		close = {
			'20260601': 50000,
			'20260531': 50000,
			'20260530': 50000,
			'20260529': 50000,
			'20260528': 50000,
			'20260527': 50000,
		}
		out = aggregate_foreign_5d_history(daily, close)
		# 20260601 기준 5일 합 (100-50+200+50+30) = 330천주 × 1000 × 50000 = 16,500,000,000
		assert out['20260601'] == 330 * 1000 * 50000
		# 20260531 기준 5일 합 (-50+200+50+30+10) = 240 × 1000 × 50000
		assert out['20260531'] == 240 * 1000 * 50000

	def test_partial_window_at_end(self):
		"""마지막 일자는 5일 미만이라도 가용 일수로 합산."""
		from modules.semi_trigger.backfill import aggregate_foreign_5d_history
		daily = [
			{'date': '20260601', 'frgnr_invsr': 100},
			{'date': '20260531', 'frgnr_invsr': 50},
		]
		close = {'20260601': 1000, '20260531': 1000}
		out = aggregate_foreign_5d_history(daily, close)
		# 20260601: (100+50) = 150 × 1000 × 1000
		assert out['20260601'] == 150_000_000
		# 20260531: 단독 50 × 1000 × 1000
		assert out['20260531'] == 50_000_000

	def test_skip_no_close(self):
		"""close 없는 날짜는 스킵."""
		from modules.semi_trigger.backfill import aggregate_foreign_5d_history
		daily = [{'date': '20260601', 'frgnr_invsr': 100}]
		close = {}
		out = aggregate_foreign_5d_history(daily, close)
		assert '20260601' not in out


class TestAggregateEtfFlowHistory:

	def test_two_underlyings(self):
		"""삼성 ETF 2종 + 하이닉스 ETF 1종 — 기초종목별 일별 합산."""
		from modules.semi_trigger.backfill import aggregate_etf_flow_history
		etf_candles = {
			'491220': [  # 삼성
				{'date': '20260601', 'trade_amount': 100},
				{'date': '20260531', 'trade_amount': 200},
			],
			'491820': [  # 삼성
				{'date': '20260601', 'trade_amount': 50},
			],
			'491230': [  # 하이닉스
				{'date': '20260601', 'trade_amount': 30},
			],
		}
		r = aggregate_etf_flow_history(etf_candles)
		# 005930 20260601: 100 + 50 = 150M × 1M = 150,000,000원
		assert r['005930']['20260601'] == 150_000_000
		# 005930 20260531: 200M × 1M
		assert r['005930']['20260531'] == 200_000_000
		# 000660 20260601: 30M × 1M
		assert r['000660']['20260601'] == 30_000_000

	def test_empty_candles(self):
		from modules.semi_trigger.backfill import aggregate_etf_flow_history
		r = aggregate_etf_flow_history({})
		assert r['005930'] == {}
		assert r['000660'] == {}

	def test_unmapped_etf_excluded(self):
		from modules.semi_trigger.backfill import aggregate_etf_flow_history
		etf_candles = {
			'999999': [{'date': '20260601', 'trade_amount': 1000}],  # 미등록
			'491220': [{'date': '20260601', 'trade_amount': 100}],
		}
		r = aggregate_etf_flow_history(etf_candles)
		assert r['005930']['20260601'] == 100_000_000
		# 999999는 어디에도 없음
