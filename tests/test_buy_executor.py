"""buy_executor pure helpers 단위 테스트.

5/25 사고 진단 후 트리거 시점 15~50 + raw 로깅 보강.
지시서 항목:
  1. should_trigger_at_open: second=10 → False (< 15)
  2. should_trigger_at_open: second=15 → True
  3. should_trigger_at_open: second=49 → True
  4. should_trigger_at_open: second=51 → False (>= 50)
  5. fetch_valid_price: 0 응답 시 raw warning 로그 출력 확인
"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestFetchValidPrice:
	"""3회 재시도 가격 조회 + 0 응답 raw 로깅."""

	def test_first_attempt_success(self):
		"""첫 시도에 (cur_prc>0, prev_close>0) 응답 → attempts=1, sleep 호출 0회."""
		from modules.buy_executor import fetch_valid_price

		calls = []
		sleep_calls = []

		async def mock_stock_info(code, token=None, silent=False):
			calls.append(code)
			return {'cur_prc': 70000.0, 'prev_close_price': 69000.0, 'raw': {}}

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
		assert attempts == 1
		assert len(calls) == 1
		assert len(sleep_calls) == 0

	def test_third_attempt_success(self):
		"""1, 2회 0 응답 → 3번째 성공. attempts=3, sleep 호출 2회."""
		from modules.buy_executor import fetch_valid_price

		responses = [
			{'cur_prc': 0, 'prev_close_price': 0, 'raw': {}},
			{'cur_prc': 70000.0, 'prev_close_price': 0, 'raw': {}},
			{'cur_prc': 70000.0, 'prev_close_price': 69000.0, 'raw': {}},
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
		assert sleep_calls == [2, 2]

	def test_all_three_failures(self):
		"""3회 모두 0 응답 → attempts=3, 값 0, 호출자가 failed_no_price 판정 가능."""
		from modules.buy_executor import fetch_valid_price

		call_count = [0]

		async def mock_stock_info(code, token=None, silent=False):
			call_count[0] += 1
			return {'cur_prc': 0, 'prev_close_price': 0, 'raw': {}}

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

	def test_zero_response_logs_raw_warning(self, caplog):
		"""0 응답 시 raw 데이터 warning 로그 강제 출력 — 사고 진단용.

		5/25 사고에서 silent=True라 raw 응답 로그 부재 → 어느 필드가 0인지 진단 불가.
		이제 0 응답 시점마다 raw warning 로그로 cur_prc/base_pric/open_pric/return_code 캡처.
		"""
		from modules.buy_executor import fetch_valid_price

		async def mock_stock_info(code, token=None, silent=False):
			return {
				'cur_prc': 0,
				'prev_close_price': 0,
				'raw': {
					'cur_prc': '0',
					'base_pric': '',
					'open_pric': None,
					'return_code': 0,
					'return_msg': '정상',
				},
			}

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

		with caplog.at_level(logging.WARNING, logger='modules.buy_executor'):
			asyncio.run(_run())

		# 3회 모두 실패 → warning 로그 3건
		warnings = [r for r in caplog.records if r.levelno == logging.WARNING and 'ka10001 0 응답' in r.getMessage()]
		assert len(warnings) == 3, f"warning 3건 기대, 실제 {len(warnings)}건"
		# raw 필드들이 메시지에 포함됐는지
		for w in warnings:
			msg = w.getMessage()
			assert 'cur_prc' in msg
			assert 'base_pric' in msg
			assert 'return_code' in msg


class TestRoundDownToTick:
	"""호가 단위 내림 — 5/26 rc=20 (주문단가 잘못) 사고 회피."""

	def test_under_2000_won_tick_1(self):
		"""1,000원대 → 호가 1원, 그대로."""
		from modules.buy_executor import round_down_to_tick
		assert round_down_to_tick(1234) == 1234

	def test_2k_5k_tick_5(self):
		"""2,000~5,000원대 → 호가 5원."""
		from modules.buy_executor import round_down_to_tick
		assert round_down_to_tick(3333) == 3330
		assert round_down_to_tick(3335) == 3335

	def test_5k_20k_tick_10(self):
		"""5,000~20,000원대 → 호가 10원."""
		from modules.buy_executor import round_down_to_tick
		assert round_down_to_tick(15555) == 15550

	def test_50k_200k_tick_100(self):
		"""50,000~200,000원대 → 호가 100원."""
		from modules.buy_executor import round_down_to_tick
		assert round_down_to_tick(123456) == 123400

	def test_200k_500k_tick_500(self):
		"""200,000~500,000원대 → 호가 500원 (005930 가격대)."""
		from modules.buy_executor import round_down_to_tick
		assert round_down_to_tick(295150) == 295000
		assert round_down_to_tick(292500) == 292500  # 이미 호가 맞음

	def test_over_500k_tick_1000(self):
		"""500,000원 이상 → 호가 1,000원."""
		from modules.buy_executor import round_down_to_tick
		assert round_down_to_tick(750555) == 750000

	def test_zero_returns_zero(self):
		"""0 입력 → 0 반환 (안전망)."""
		from modules.buy_executor import round_down_to_tick
		assert round_down_to_tick(0) == 0


class TestShouldTriggerAtOpen:
	"""09:00 트리거 시점 — 5/25 사고 후 15~50초 윈도우로 확대."""

	def test_too_early_09_00_10_false(self):
		"""09:00:10은 second < 15라 트리거 안 됨 (5/25 시초가 미반영 구간)."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 26, 9, 0, 10)
		assert should_trigger_at_open(now, executed_today=False) is False

	def test_09_00_15_true(self):
		"""09:00:15은 second >= 15, < 50, executed_today=False라 트리거."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 26, 9, 0, 15)
		assert should_trigger_at_open(now, executed_today=False) is True

	def test_09_00_49_true(self):
		"""09:00:49는 윈도우 끝 직전, 여전히 트리거."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 26, 9, 0, 49)
		assert should_trigger_at_open(now, executed_today=False) is True

	def test_too_late_09_00_51_false(self):
		"""09:00:51은 second >= 50이라 트리거 안 됨 (시한 초과)."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 26, 9, 0, 51)
		assert should_trigger_at_open(now, executed_today=False) is False

	def test_executed_today_blocks(self):
		"""executed_today=True면 시각 무관 False (중복 매수 방지)."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 26, 9, 0, 20)
		assert should_trigger_at_open(now, executed_today=True) is False

	def test_other_hour_false(self):
		"""10:00:20은 hour 다름 → False."""
		from modules.buy_executor import should_trigger_at_open
		now = datetime(2026, 5, 26, 10, 0, 20)
		assert should_trigger_at_open(now, executed_today=False) is False
