"""
풀 모니터링용 인메모리 데이터 버퍼.

0B push 데이터(가격/체결강도/거래량)를 메모리에 누적, 30초 간격으로 디스크 flush.
풀 종료 시점에는 명시적 flush + 메모리 제거.

사용:
    from sector.pool_buffer import get_buffer
    buf = get_buffer()
    buf.append(code, price, strength, volume)
    history = buf.get_history(code)
    await buf.maybe_flush()  # 30초 경과 시 자동 flush
    buf.remove(code)         # 풀 종료 시
"""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 상수
FLUSH_INTERVAL_SEC = 30
HISTORY_MAX_LEN = 600  # 0B push 빈도 가정 시 충분한 여유 (수십분치)

BUFFER_DIR = Path('logs/pool_buffer')


class PoolBuffer:
	"""0B push 데이터 인메모리 누적 + 주기적 flush."""

	def __init__(self):
		# code -> {'price': deque[(datetime, float)], 'strength': deque, 'volume': deque, 'last_update': datetime}
		self._memory = defaultdict(lambda: {
			'price': deque(maxlen=HISTORY_MAX_LEN),
			'strength': deque(maxlen=HISTORY_MAX_LEN),
			'volume': deque(maxlen=HISTORY_MAX_LEN),
			'last_update': None,
		})
		self._last_flush = time.time()
		self._flush_lock = asyncio.Lock()
		BUFFER_DIR.mkdir(parents=True, exist_ok=True)

	def append(self, code: str, price: float, strength: float, volume: int):
		"""0B push 데이터 1건 누적."""
		now = datetime.now()
		d = self._memory[code]
		d['price'].append((now, price))
		d['strength'].append((now, strength))
		d['volume'].append((now, volume))
		d['last_update'] = now

	def get_history(self, code: str) -> Optional[dict]:
		"""누적 history 조회. 없으면 None."""
		return self._memory.get(code, None)

	def remove(self, code: str):
		"""풀 종료 시 해당 종목 데이터 제거 (메모리 정리)."""
		self._memory.pop(code, None)

	async def maybe_flush(self):
		"""30초 경과 시 flush. 호출자가 주기적으로 호출."""
		now = time.time()
		if now - self._last_flush < FLUSH_INTERVAL_SEC:
			return
		await self.flush()

	async def flush(self):
		"""현재 인메모리 데이터 디스크 저장."""
		async with self._flush_lock:
			try:
				date_str = datetime.now().strftime('%Y-%m-%d')
				path = BUFFER_DIR / f'buffer_{date_str}.json'

				# deque는 JSON 직렬화 불가 → list 변환
				snapshot = {}
				for code, d in self._memory.items():
					snapshot[code] = {
						'price': [(t.isoformat(), v) for t, v in d['price']],
						'strength': [(t.isoformat(), v) for t, v in d['strength']],
						'volume': [(t.isoformat(), v) for t, v in d['volume']],
						'last_update': d['last_update'].isoformat() if d['last_update'] else None,
					}

				tmp_path = path.with_suffix(path.suffix + '.tmp')
				with tmp_path.open('w', encoding='utf-8') as f:
					json.dump(snapshot, f, ensure_ascii=False)
				tmp_path.replace(path)

				self._last_flush = time.time()
			except Exception:
				logger.exception("PoolBuffer flush failed")


# 봇 전역 인스턴스 (싱글톤)
_global_buffer: Optional[PoolBuffer] = None


def get_buffer() -> PoolBuffer:
	global _global_buffer
	if _global_buffer is None:
		_global_buffer = PoolBuffer()
	return _global_buffer
