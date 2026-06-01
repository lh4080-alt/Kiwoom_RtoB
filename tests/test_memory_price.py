"""⑤ 메모리 가격 collector — carry-forward 헬퍼 + Phase 5 가중 재분배 통합."""
import asyncio
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestCarryForward:

	def test_most_recent_non_none(self):
		from modules.semi_trigger.collectors.memory_price import carry_forward
		history = [
			{'date': '20260601', 'memory_price': None},
			{'date': '20260531', 'memory_price': None},
			{'date': '20260530', 'memory_price': 1.5},  # 이게 carry-forward 됨
			{'date': '20260529', 'memory_price': 2.0},
		]
		assert carry_forward(history) == 1.5

	def test_all_none(self):
		from modules.semi_trigger.collectors.memory_price import carry_forward
		history = [{'date': '20260601', 'memory_price': None}]
		assert carry_forward(history) is None

	def test_empty(self):
		from modules.semi_trigger.collectors.memory_price import carry_forward
		assert carry_forward([]) is None

	def test_first_value(self):
		from modules.semi_trigger.collectors.memory_price import carry_forward
		history = [{'date': '20260601', 'memory_price': 0.5}]
		assert carry_forward(history) == 0.5


class TestCollectMemoryPrice:

	def test_returns_none(self):
		"""현재 미구현 → 항상 None + is_carry_forward=False."""
		from modules.semi_trigger.collectors.memory_price import collect_memory_price
		r = asyncio.run(collect_memory_price())
		assert r['memory_price'] is None
		assert r['source'] is None
		assert r['is_carry_forward'] is False


class TestMemoryPriceMissingFallback:
	"""⑤ 결측 시 scoring.calc_semi_score가 가중 재분배 — 통합 검증."""

	def test_semi_score_with_memory_price_none(self):
		"""us_memory/etf_flow/fx/foreign_flow만 z 있음 → 정상 점수 산출."""
		from modules.semi_trigger.scoring import calc_semi_score
		r = calc_semi_score({
			'us_memory': 2.0,       # 0.40 → 0.4444
			'etf_flow': 1.5,        # 0.20 → 0.2222
			'fx': 0.5,              # 0.20 → 0.2222
			'foreign_flow': 1.0,    # 0.10 → 0.1111
			'memory_price': None,   # 결측 → 재분배
		})
		assert r['semi_score'] is not None
		assert r['weight_redistributed'] is True
		assert 'memory_price' not in r['used_axes']
		assert len(r['used_axes']) == 4

	def test_all_axes_none_returns_none(self):
		from modules.semi_trigger.scoring import calc_semi_score
		r = calc_semi_score({k: None for k in
		                     ('us_memory', 'etf_flow', 'fx', 'foreign_flow', 'memory_price')})
		assert r['semi_score'] is None
