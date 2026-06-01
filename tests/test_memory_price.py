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
	"""⑤ nasdaq_futures 결측 시 scoring 가중 재분배 (Lee 6/2: memory_price → NQ 교체)."""

	def test_semi_score_with_nq_none(self):
		"""4축 시스템 — nasdaq_futures 결측 → 3축 가중 재분배."""
		from modules.semi_trigger.scoring import calc_semi_score
		r = calc_semi_score({
			'us_memory':        2.0,
			'legacy_sox_nvda':  1.0,
			'fx':               0.5,
			'nasdaq_futures':   None,
		})
		assert r['semi_score'] is not None
		assert r['weight_redistributed'] is True
		assert 'nasdaq_futures' not in r['used_axes']
		assert len(r['used_axes']) == 3

	def test_all_axes_none_returns_none(self):
		from modules.semi_trigger.scoring import calc_semi_score
		r = calc_semi_score({k: None for k in
		                     ('us_memory', 'legacy_sox_nvda', 'fx', 'nasdaq_futures')})
		assert r['semi_score'] is None
