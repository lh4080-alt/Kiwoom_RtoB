"""buy_executor pure helpers 단위 테스트.

5/21 GST(083450) failed_no_price 사고 회피용 보강 검증:
  - 09:00:00 직후 키움 API가 시초가 0으로 응답하는 케이스 → 3회 재시도
  - _scheduler_loop 트리거를 09:00:05 이후로 지연

테스트 항목 (지시서):
  1. fetch_valid_price 첫 시도 성공 (attempts=1)
  2. fetch_valid_price 2회 실패 후 3번째 성공 (attempts=3, sleep 2번)
  3. fetch_valid_price 3회 모두 0 (attempts=3, failed)
  4. should_trigger_at_open 09:00:03 → False (second < 5)
  5. should_trigger_at_open 09:00:07 → True
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestFetchValidPrice:
	"""3회 재시도 가격 조회."""

	def test_first_attempt_success(self):
		"""첫 시도에 (cur_prc>0, prev_close>0) 응답 → attempts=1, sleep 호출 0회."""
		from modules.buy_executor import fetch_valid_price

		calls = []
		sleep_calls = []

		async def mock_stock_info(code, token=None, silent=False):
			calls.append(code)
			return {'cur_prc': 70000.0, 'prev_close_price': 69000.0}

		async def mock_sleep(sec):
			sleep_calls.append(sec)

		async def _run():
			# asyncio.sleep을 mock으로 우회
			import modules.buy_executor as bx
			original_sleep = bx.asyncio.sleep
			bx.asyncio.sleep = mock_sleep
			try:
				return await fetch_valid_price('005930', 'TOKEN', mock_stock_info)
			finally:
				bx.asyncio.sleep = original_sleep

		open_p, prev, attempts = asyncio.run(_run())
		assert open_p == 70000.0
		assert prev == 69000.0
		assert attempts == 1
		assert len(calls) == 1
		assert len(sleep_calls) == 0  # 성공이라 sleep 안 함

	def test_third_attempt_success(self):
		"""1, 2회 0 응답 → 3번째 성공. attempts=3, sleep 호출 2회."""
		from modules.buy_executor import fetch_valid_price

		responses = [
			{'cur_prc': 0, 'prev_close_price': 0},          # 1회: 시초가 미반영
			{'cur_prc': 70000.0, 'prev_close_price': 0},    # 2회: prev 미반영
			{'cur_prc': 70000.0, 'prev_close_price': 69000.0},  # 3회: 정상
		]
		idx = [0]
		sleep_calls = []

		async def mock_stock_info(code, token=None, silent=False):
			r = responses[idx[0]]
			idx[0] += 1
			return r

		async def mock_sleep(sec):
			sleep_calls.append(sec)

		async def _run():
			import modules.buy_executor as bx
			original_sleep = bx.asyncio.sleep
			bx.asyncio.sleep = mock_sleep
			try:
				return await fetch_valid_price('005930', 'TOKEN', mock_stock_info)
			finally:
				bx.asyncio.sleep = original_sleep

		open_p, prev, attempts = asyncio.run(_run())
		assert open_p == 70000.0
		assert prev == 69000.0
		assert attempts == 3
		assert idx[0] == 3
		assert sleep_calls == [2, 2]  # 1→2, 2→3 사이 2번 sleep, 각 2초

	def test_all_three_failures(self):
		"""3회 모두 0 응답 → attempts=3, 값은 0, 호출자가 failed_no_price 판정 가능."""
		from modules.buy_executor import fetch_valid_price

		call_count = [0]

		async def mock_stock_info(code, token=None, silent=False):
			call_count[0] += 1
			return {'cur_prc': 0, 'prev_close_price': 0}

		async def mock_sleep(sec):
			pass

		async def _run():
			import modules.buy_executor as bx
			original_sleep = bx.asyncio.sleep
			bx.asyncio.sleep = mock_sleep
			try:
				return await fetch_valid_price('005930', 'TOKEN', mock_stock_info)
			finally:
				bx.asyncio.sleep = original_sleep

		open_p, prev, attempts = asyncio.run(_run())
		assert open_p == 0
		assert prev == 0
		assert attempts == 3
		assert call_count[0] == 3


class TestShouldTriggerAtOpen:
	"""09:00 트리거 시점 — 09:00:05 이후 첫 폴링."""

	def test_too_early_09_00_03_false(self):
		"""09:00:03은 second < 5라 트리거 안 됨 (시초가 데이터 미안정)."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 22, 9, 0, 3)
		assert should_trigger_at_open(now, executed_today=False) is False

	def test_09_00_07_true(self):
		"""09:00:07은 second >= 5이고 < 30, executed_today=False라 트리거."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 22, 9, 0, 7)
		assert should_trigger_at_open(now, executed_today=False) is True

	def test_executed_today_blocks(self):
		"""executed_today=True면 시각 무관 False (중복 매수 방지)."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 22, 9, 0, 10)
		assert should_trigger_at_open(now, executed_today=True) is False

	def test_too_late_09_00_30_false(self):
		"""09:00:30은 second >= 30이라 트리거 안 됨 (시한 초과)."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 22, 9, 0, 30)
		assert should_trigger_at_open(now, executed_today=False) is False

	def test_other_hour_false(self):
		"""10:00:10은 hour 다름 → False."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 22, 10, 0, 10)
		assert should_trigger_at_open(now, executed_today=False) is False
