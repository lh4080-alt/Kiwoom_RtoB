"""buy_executor pure helpers 단위 테스트.

5/26 통합 후 폐기된 헬퍼 (fetch_valid_price, round_down_to_tick) 테스트 제거.
유지: should_trigger_at_open (09:00 트리거 윈도우 15~50).
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestShouldTriggerAtOpen:
	"""09:00 트리거 시점 — 15~50초 윈도우."""

	def test_too_early_09_00_10_false(self):
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 27, 9, 0, 10)
		assert should_trigger_at_open(now, executed_today=False) is False

	def test_09_00_15_true(self):
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 27, 9, 0, 15)
		assert should_trigger_at_open(now, executed_today=False) is True

	def test_09_00_49_true(self):
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 27, 9, 0, 49)
		assert should_trigger_at_open(now, executed_today=False) is True

	def test_too_late_09_00_51_false(self):
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 27, 9, 0, 51)
		assert should_trigger_at_open(now, executed_today=False) is False

	def test_executed_today_blocks(self):
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 27, 9, 0, 20)
		assert should_trigger_at_open(now, executed_today=True) is False

	def test_other_hour_false(self):
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 27, 10, 0, 20)
		assert should_trigger_at_open(now, executed_today=False) is False
