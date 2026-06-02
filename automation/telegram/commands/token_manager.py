import asyncio
import sys
import os
from datetime import datetime, timedelta

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.login import fn_au10001

# 키움 토큰 유효 시간 — 발급 후 N시간 경과 시 자동 강제 갱신 (안전 마진)
TOKEN_MAX_AGE_HOURS = 20


class TokenManager:
	"""토큰 관리를 담당하는 클래스.

	09:00 부근 race 방지: get_token에 asyncio.Lock 적용 → 동시 발급 직렬화.
	main.py auto_start와 BuyExecutor가 같은 시각에 reset_token 후 발급 호출해도
	하나가 발급 끝낼 때까지 다른 하나는 대기, 발급된 토큰을 재사용.

	시간 기반 자동 갱신 (6/2 추가): TOKEN_MAX_AGE_HOURS 경과 시 자동 force_refresh.
	모든 호출처가 자동 보호됨 (auction/buy_executor/watching_buyer/stick_executor 등).
	"""

	def __init__(self):
		self.token = None  # 현재 사용 중인 토큰
		self._issued_at = None  # 토큰 발급 시각
		self._lock = asyncio.Lock()

	def reset_token(self):
		"""기존 토큰을 초기화합니다. 모드 전환 시 사용됩니다."""
		self.token = None
		self._issued_at = None
		print("토큰이 초기화되었습니다.")

	def _is_expired_by_time(self):
		"""발급 후 N시간 경과 여부."""
		if self._issued_at is None:
			return True
		return (datetime.now() - self._issued_at) > timedelta(hours=TOKEN_MAX_AGE_HOURS)

	async def get_token(self, force_refresh=False):
		"""토큰을 가져옵니다. lock으로 동시 발급 방지 + 시간 기반 자동 갱신.

		갱신 조건:
		  - force_refresh=True
		  - self.token이 None
		  - 발급 후 TOKEN_MAX_AGE_HOURS (20시간) 경과
		"""
		async with self._lock:
			# Lock 안에서 다시 확인 — 다른 코루틴이 이미 발급했을 수 있음
			need_refresh = force_refresh or not self.token or self._is_expired_by_time()
			if not need_refresh:
				return self.token
			try:
				token = await fn_au10001()
				if token:
					old_token = self.token[:10] + "..." if self.token else "없음"
					self.token = token
					self._issued_at = datetime.now()
					if force_refresh:
						print(f"강제 토큰 갱신 완료: {old_token} -> {token[:10]}...")
					elif old_token != "없음":
						print(f"시간 만료 자동 갱신: {old_token} -> {token[:10]}...")
					else:
						print(f"새로운 토큰 발급 완료: {token[:10]}...")
					return token
				else:
					print("토큰 발급 실패")
					return None
			except Exception as e:
				# e가 비어 보이는 경우가 있어 타입/repr까지 출력
				print(f"토큰 발급 중 오류: {type(e).__name__}: {e!r}")
				return None

	async def call_with_auto_refresh(self, api_fn, *args, **kwargs):
		"""API 함수 호출 + return_code=3 (토큰 무효) 감지 시 force_refresh + 1회 재시도.

		5/22, 5/26 사고 패턴: 봇 self.token이 키움에서 invalidate된 상태로 사용 → 401.
		token_manager Lock은 동시 발급 race만 막음, 무효 토큰 감지는 별도 안전망 필요.

		Args:
			api_fn: token kwarg을 받는 비동기 함수 (예: fn_ka10001, fn_ka10046).
			        반환은 dict이고 'return_code' 키 (또는 'raw.return_code') 포함 가정.
			*args, **kwargs: api_fn에 그대로 전달. token은 자동 주입.

		Returns:
			api_fn 결과 dict.
		"""
		token = await self.get_token()
		kwargs['token'] = token
		result = await api_fn(*args, **kwargs)

		rc = _extract_rc(result)
		if rc in (3, '3'):
			# 토큰 무효 — force_refresh + 재시도
			print(f"[token_manager] rc=3 (토큰 무효) 감지 — force_refresh 후 1회 재시도")
			new_token = await self.get_token(force_refresh=True)
			kwargs['token'] = new_token
			result = await api_fn(*args, **kwargs)

		return result


def _extract_rc(result):
	"""dict 결과에서 return_code 추출 — 최상위 또는 'raw' 안쪽 둘 다 지원."""
	if not isinstance(result, dict):
		return None
	rc = result.get('return_code')
	if rc is not None:
		return rc
	raw = result.get('raw')
	if isinstance(raw, dict):
		return raw.get('return_code')
	return None

