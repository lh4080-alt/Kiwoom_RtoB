"""
HTTP 요청 Rate Limiter (비동기)
1초에 5번 이상의 HTTP 요청이 가지 않도록 제한합니다. (분당 300번)
"""
import asyncio
import time
from collections import deque
import httpx


class AsyncRateLimiter:
	"""
	HTTP 요청을 1초에 최대 5번으로 제한하는 비동기 Rate Limiter
	분당 300번 제한 유지
	"""
	def __init__(self, max_requests=5, time_window=1.0):
		"""
		Args:
			max_requests: 시간 윈도우 내 최대 요청 수
			time_window: 시간 윈도우 (초)
		"""
		self.max_requests = max_requests
		self.time_window = time_window
		self.request_times = deque()
		self.lock = asyncio.Lock()
		self._client = None
	
	async def _get_client(self):
		"""httpx 클라이언트를 싱글톤으로 관리"""
		if self._client is None:
			self._client = httpx.AsyncClient(timeout=30.0)
		return self._client
	
	async def _wait_if_needed(self):
		"""필요한 경우 요청 전 대기 (비동기)"""
		async with self.lock:
			now = time.time()
			
			# 시간 윈도우 밖의 오래된 요청 기록 제거
			while self.request_times and self.request_times[0] < now - self.time_window:
				self.request_times.popleft()
			
			# 최대 요청 수에 도달한 경우 대기
			if len(self.request_times) >= self.max_requests:
				# 가장 오래된 요청이 시간 윈도우를 벗어날 때까지 대기
				oldest_request_time = self.request_times[0]
				wait_time = self.time_window - (now - oldest_request_time) + 0.01  # 0.01초 여유
				if wait_time > 0:
					await asyncio.sleep(wait_time)
					now = time.time()
					# 다시 오래된 요청 기록 제거
					while self.request_times and self.request_times[0] < now - self.time_window:
						self.request_times.popleft()
			
			# 현재 요청 시간 기록
			self.request_times.append(time.time())
	
	async def get(self, *args, **kwargs):
		"""httpx.get을 래핑 (비동기)"""
		await self._wait_if_needed()
		client = await self._get_client()
		return await client.get(*args, **kwargs)
	
	async def post(self, *args, **kwargs):
		"""httpx.post을 래핑 (비동기)"""
		await self._wait_if_needed()
		client = await self._get_client()
		return await client.post(*args, **kwargs)
	
	async def put(self, *args, **kwargs):
		"""httpx.put을 래핑 (비동기)"""
		await self._wait_if_needed()
		client = await self._get_client()
		return await client.put(*args, **kwargs)
	
	async def delete(self, *args, **kwargs):
		"""httpx.delete을 래핑 (비동기)"""
		await self._wait_if_needed()
		client = await self._get_client()
		return await client.delete(*args, **kwargs)
	
	async def patch(self, *args, **kwargs):
		"""httpx.patch을 래핑 (비동기)"""
		await self._wait_if_needed()
		client = await self._get_client()
		return await client.patch(*args, **kwargs)
	
	async def head(self, *args, **kwargs):
		"""httpx.head을 래핑 (비동기)"""
		await self._wait_if_needed()
		client = await self._get_client()
		return await client.head(*args, **kwargs)
	
	async def options(self, *args, **kwargs):
		"""httpx.options을 래핑 (비동기)"""
		await self._wait_if_needed()
		client = await self._get_client()
		return await client.options(*args, **kwargs)
	
	async def close(self):
		"""httpx 클라이언트 종료"""
		if self._client:
			await self._client.aclose()
			self._client = None


# 전역 AsyncRateLimiter 인스턴스 생성
_rate_limiter = AsyncRateLimiter(max_requests=5, time_window=1.0)

# httpx 모듈과 유사한 인터페이스를 제공하는 객체
class AsyncRequestsWrapper:
	"""httpx 모듈을 래핑하여 rate limiting을 적용"""
	
	@property
	def get(self):
		return _rate_limiter.get
	
	@property
	def post(self):
		return _rate_limiter.post
	
	@property
	def put(self):
		return _rate_limiter.put
	
	@property
	def delete(self):
		return _rate_limiter.delete
	
	@property
	def patch(self):
		return _rate_limiter.patch
	
	@property
	def head(self):
		return _rate_limiter.head
	
	@property
	def options(self):
		return _rate_limiter.options
	
	async def close(self):
		"""클라이언트 종료"""
		await _rate_limiter.close()


# requests를 대체할 비동기 객체
requests = AsyncRequestsWrapper()

