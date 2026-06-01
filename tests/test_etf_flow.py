"""② 단일ETF 거래대금 collector — aggregate_etf_flows 단위 테스트."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestAggregateEtfFlows:

	def test_normal_aggregation(self):
		"""삼성 ETF 2종 + 하이닉스 ETF 1종 — 기초종목별 합산."""
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		etf_to_candles = {
			'491220': [{'date': '20260601', 'trade_amount': 762}],     # 삼성, 762M원
			'491410': [{'date': '20260601', 'trade_amount': 500}],     # 삼성, 500M원
			'491230': [{'date': '20260601', 'trade_amount': 114}],     # 하이닉스
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		# 005930: 762M + 500M = 1,262,000,000원
		assert r['005930']['etf_flow'] == 1_262_000_000
		assert r['005930']['etfs_count'] == 2
		assert {e['code'] for e in r['005930']['etfs']} == {'491220', '491410'}
		# 000660: 114M = 114,000,000원
		assert r['000660']['etf_flow'] == 114_000_000
		assert r['000660']['etfs_count'] == 1

	def test_empty_input(self):
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		r = aggregate_etf_flows({}, '20260601')
		assert r['005930']['etf_flow'] == 0
		assert r['000660']['etf_flow'] == 0
		assert r['005930']['etfs_count'] == 0

	def test_date_mismatch_skipped(self):
		"""base_dt와 일치 안 하는 candle은 합산에서 제외."""
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		etf_to_candles = {
			'491220': [{'date': '20260530', 'trade_amount': 100}],  # 다른 날짜
			'491410': [{'date': '20260601', 'trade_amount': 500}],  # 일치
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		# 491410만 합산
		assert r['005930']['etf_flow'] == 500_000_000
		assert r['005930']['etfs_count'] == 1

	def test_multi_day_candles_picks_base_dt(self):
		"""여러 일 candles 중 base_dt 일치하는 것만 픽."""
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		etf_to_candles = {
			'491220': [
				{'date': '20260601', 'trade_amount': 700},
				{'date': '20260531', 'trade_amount': 999},
				{'date': '20260530', 'trade_amount': 888},
			]
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		assert r['005930']['etf_flow'] == 700_000_000

	def test_empty_candles_list_skipped(self):
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		etf_to_candles = {
			'491220': [],  # 빈
			'491410': [{'date': '20260601', 'trade_amount': 500}],
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		assert r['005930']['etf_flow'] == 500_000_000

	def test_unmapped_etf_skipped(self):
		"""ETF_TO_UNDERLYING에 없는 코드는 합산 제외."""
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		etf_to_candles = {
			'999999': [{'date': '20260601', 'trade_amount': 1000}],  # 미등록
			'491220': [{'date': '20260601', 'trade_amount': 500}],
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		assert r['005930']['etf_flow'] == 500_000_000
		# 999999는 어디에도 합산 안 됨

	def test_unit_conversion_million_to_won(self):
		"""trde_prica (백만원) × 1M = 원."""
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		etf_to_candles = {
			'491220': [{'date': '20260601', 'trade_amount': 1}],  # 1 백만원
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		assert r['005930']['etf_flow'] == 1_000_000  # 100만원

	def test_all_14_etfs_present(self):
		"""14종 ETF 모두 응답 시 005930/000660 각 7종 합산."""
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		from modules.semi_trigger.etf_mapping import ETF_TO_UNDERLYING

		etf_to_candles = {
			code: [{'date': '20260601', 'trade_amount': 100}]
			for code in ETF_TO_UNDERLYING
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		# 7종 × 100M = 700M = 700,000,000원
		assert r['005930']['etfs_count'] == 7
		assert r['005930']['etf_flow'] == 700_000_000
		assert r['000660']['etfs_count'] == 7
		assert r['000660']['etf_flow'] == 700_000_000

	def test_trade_amount_zero(self):
		from modules.semi_trigger.collectors.etf_flow import aggregate_etf_flows
		etf_to_candles = {
			'491220': [{'date': '20260601', 'trade_amount': 0}],
		}
		r = aggregate_etf_flows(etf_to_candles, '20260601')
		# 0 × 1M = 0 (etf 자체는 카운트됨)
		assert r['005930']['etf_flow'] == 0
		assert r['005930']['etfs_count'] == 1
