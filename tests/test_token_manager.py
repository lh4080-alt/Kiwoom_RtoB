"""TokenManager 동시 호출 race 방지 테스트.

5/22 09:00 사고 분석 후 추가된 asyncio.Lock 검증:
  - 같은 시각 여러 코루틴이 get_token() 호출해도 fn_au10001은 1회만 호출
  - 두 번째 이후 호출은 첫 번째가 발급한 토큰 재사용
"""
import asyncio
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestTokenManagerLock:

	def test_concurrent_get_token_single_issue(self):
		"""동시 5개 코루틴이 get_token 호출 → fn_au10001은 1회만 실행.

		Lock 없으면 5번 발급 (race), Lock 있으면 첫 번째가 발급 후 나머지는 self.token 재사용.
		"""
		from telegram.commands import token_manager as tm_module
		from telegram.commands.token_manager import TokenManager

		call_count = [0]

		async def mock_fn_au10001():
			call_count[0] += 1
			await asyncio.sleep(0.05)  # 발급 지연 시뮬레이션 (race 윈도우 늘림)
			return f"TOKEN_{call_count[0]}"

		# fn_au10001 모듈 수준 함수 mock
		original = tm_module.fn_au10001
		tm_module.fn_au10001 = mock_fn_au10001

		try:
			async def _run():
				tm = TokenManager()
				# 5개 코루틴 동시 시작
				tokens = await asyncio.gather(*[tm.get_token() for _ in range(5)])
				return tokens, tm.token

			tokens, final_token = asyncio.run(_run())
		finally:
			tm_module.fn_au10001 = original

		# 모두 같은 토큰 (첫 발급분 재사용)
		assert all(t == tokens[0] for t in tokens), f"tokens not identical: {tokens}"
		# fn_au10001은 1회만 호출
		assert call_count[0] == 1, f"fn_au10001 called {call_count[0]} times (expected 1)"
		# self.token도 같은 값
		assert final_token == tokens[0]

	def test_force_refresh_after_reset(self):
		"""reset_token 후 get_token 호출 → 새 발급 정상 동작."""
		from telegram.commands import token_manager as tm_module
		from telegram.commands.token_manager import TokenManager

		call_count = [0]

		async def mock_fn_au10001():
			call_count[0] += 1
			return f"NEW_TOKEN_{call_count[0]}"

		original = tm_module.fn_au10001
		tm_module.fn_au10001 = mock_fn_au10001

		try:
			async def _run():
				tm = TokenManager()
				t1 = await tm.get_token()
				tm.reset_token()
				assert tm.token is None
				t2 = await tm.get_token()  # 재발급
				return t1, t2

			t1, t2 = asyncio.run(_run())
		finally:
			tm_module.fn_au10001 = original

		assert t1 != t2
		assert call_count[0] == 2

	def test_call_with_auto_refresh_on_rc3(self):
		"""5/26 16:00 daily 사고 회피 검증 — API 응답 rc=3이면 force_refresh + 재시도."""
		from telegram.commands import token_manager as tm_module
		from telegram.commands.token_manager import TokenManager

		issue_count = [0]

		async def mock_fn_au10001():
			issue_count[0] += 1
			return f"TOKEN_{issue_count[0]}"

		original = tm_module.fn_au10001
		tm_module.fn_au10001 = mock_fn_au10001

		try:
			# api_fn: 첫 호출은 rc=3 (토큰 무효), 두 번째는 rc=0 정상
			call_count = [0]
			tokens_seen = []

			async def mock_api(*args, **kwargs):
				call_count[0] += 1
				tokens_seen.append(kwargs.get('token'))
				if call_count[0] == 1:
					return {'return_code': 3, 'return_msg': 'Token 무효'}
				return {'return_code': 0, 'data': 'OK'}

			async def _run():
				tm = TokenManager()
				return await tm.call_with_auto_refresh(mock_api, 'arg1', kw1='val')

			result = asyncio.run(_run())
		finally:
			tm_module.fn_au10001 = original

		assert result == {'return_code': 0, 'data': 'OK'}, f"재시도 후 정상 결과 기대, 실제 {result}"
		assert call_count[0] == 2, f"호출 2회 기대, 실제 {call_count[0]}"
		assert issue_count[0] == 2, f"토큰 2회 발급 기대 (초기+force_refresh), 실제 {issue_count[0]}"
		# 첫 호출은 TOKEN_1, 두 번째는 TOKEN_2 (force_refresh 후)
		assert tokens_seen == ['TOKEN_1', 'TOKEN_2'], f"토큰 변경 확인 실패: {tokens_seen}"

	def test_call_with_auto_refresh_no_retry_on_success(self):
		"""rc=0이면 재시도 안 함, 토큰도 재발급 안 함."""
		from telegram.commands import token_manager as tm_module
		from telegram.commands.token_manager import TokenManager

		issue_count = [0]

		async def mock_fn_au10001():
			issue_count[0] += 1
			return f"TOKEN_{issue_count[0]}"

		original = tm_module.fn_au10001
		tm_module.fn_au10001 = mock_fn_au10001

		try:
			call_count = [0]

			async def mock_api(*args, **kwargs):
				call_count[0] += 1
				return {'return_code': 0, 'data': 'OK'}

			async def _run():
				tm = TokenManager()
				return await tm.call_with_auto_refresh(mock_api)

			result = asyncio.run(_run())
		finally:
			tm_module.fn_au10001 = original

		assert result == {'return_code': 0, 'data': 'OK'}
		assert call_count[0] == 1, "rc=0이면 1회 호출"
		assert issue_count[0] == 1, "rc=0이면 초기 토큰 1회만 발급"

	def test_call_with_auto_refresh_raw_return_code(self):
		"""fn_ka10001처럼 return_code가 raw 안에 있는 경우도 추출 가능."""
		from telegram.commands import token_manager as tm_module
		from telegram.commands.token_manager import TokenManager

		async def mock_fn_au10001():
			return "TOKEN"

		original = tm_module.fn_au10001
		tm_module.fn_au10001 = mock_fn_au10001

		try:
			call_count = [0]

			async def mock_api(*args, **kwargs):
				call_count[0] += 1
				if call_count[0] == 1:
					# return_code 최상위에 없고 raw 안에만
					return {'cur_prc': 0, 'raw': {'return_code': 3}}
				return {'cur_prc': 1000, 'raw': {'return_code': 0}}

			async def _run():
				tm = TokenManager()
				return await tm.call_with_auto_refresh(mock_api)

			result = asyncio.run(_run())
		finally:
			tm_module.fn_au10001 = original

		assert result['cur_prc'] == 1000
		assert call_count[0] == 2

	def test_reset_during_get_serialized(self):
		"""reset_token + 동시 get_token race 시나리오 — 5/22 사고 케이스 재현.

		main.py auto_start가 reset → BuyExecutor가 get_token (lock 안에서 발급)
		→ 다른 코루틴이 또 get_token (lock 대기 후 self.token 재사용).
		"""
		from telegram.commands import token_manager as tm_module
		from telegram.commands.token_manager import TokenManager

		call_count = [0]

		async def mock_fn_au10001():
			call_count[0] += 1
			await asyncio.sleep(0.03)
			return f"TOKEN_{call_count[0]}"

		original = tm_module.fn_au10001
		tm_module.fn_au10001 = mock_fn_au10001

		try:
			async def _run():
				tm = TokenManager()
				# 초기 발급
				await tm.get_token()
				assert call_count[0] == 1

				# reset 후 동시 3개 코루틴이 get_token 호출
				tm.reset_token()
				tokens = await asyncio.gather(*[tm.get_token() for _ in range(3)])
				return tokens

			tokens = asyncio.run(_run())
		finally:
			tm_module.fn_au10001 = original

		# reset 후 동시 3개 호출이 1회 발급으로 직렬화
		assert all(t == tokens[0] for t in tokens), f"tokens not identical: {tokens}"
		assert call_count[0] == 2  # 초기 1 + reset 후 1 = 2 (race 없으면)
