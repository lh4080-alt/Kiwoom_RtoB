"""
조건검색 매칭 수집 (계정1 search).

장중 09:00~15:30 매칭 종목을 collection_pool에 누적.
16:00에 daily_analyzer(Step B 예정)가 collection_pool 분석 후 비움.

Phase 2 Step A: 코드 작성 + import 검증까지만.
봇 startup 활성화(start() 호출)는 Step C에서.
"""
import asyncio
import logging
from datetime import datetime, time
from typing import Optional

from utils.collection_pool import add_to_pool

logger = logging.getLogger(__name__)


class ConditionCollector:
	"""조건검색 매칭 수집 (계정1 search 사용)."""

	def __init__(self, search_client, condition_seq: int = 0):
		"""
		Args:
			search_client: KiwoomClient (search 또는 trade fallback)
			condition_seq: 조건검색 seq (settings.json의 search_seq에서 가져옴)
		"""
		self.client = search_client
		self.condition_seq = condition_seq
		self.is_active = False
		self._task: Optional[asyncio.Task] = None
		self.match_count = 0

	async def start(self):
		"""수집 task 시작 (봇 startup 시 호출)."""
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._main_loop())
			logger.info(f"ConditionCollector started (seq={self.condition_seq})")

	async def stop(self):
		"""수집 종료."""
		if self._task and not self._task.done():
			self._task.cancel()
		if self.is_active:
			await self._cancel_subscription()
		logger.info("ConditionCollector stopped")

	async def _main_loop(self):
		"""09:00 시작, 15:30 종료 자동 관리."""
		while True:
			try:
				now = datetime.now().time()
				in_market = time(9, 0) <= now <= time(15, 30)

				if in_market and not self.is_active:
					await self._start_subscription()
				elif not in_market and self.is_active:
					await self._cancel_subscription()

				await asyncio.sleep(60)  # 1분 간격 체크
			except asyncio.CancelledError:
				logger.info("ConditionCollector loop cancelled")
				raise
			except Exception:
				logger.exception("ConditionCollector loop error")
				await asyncio.sleep(60)

	async def _start_subscription(self):
		"""CNSRREQ 등록 + WebSocket 핸들러 시작."""
		try:
			# 1. 조건검색 목록 조회 (CNSRLST) — 안전성 보장 위해
			await self.client.send_websocket({'trnm': 'CNSRLST'})
			await asyncio.sleep(1)

			# 2. CNSRREQ 등록 (search_type=1, 초기 스냅샷 + 실시간)
			await self.client.send_websocket({
				'trnm': 'CNSRREQ',
				'seq': str(self.condition_seq),
				'search_type': '1',
				'stex_tp': 'K',
			})
			self.is_active = True
			logger.info(f"조건검색 등록 완료 (seq={self.condition_seq})")

			# 3. WebSocket 메시지 핸들러 등록 — search_client에 콜백 추가
			self.client.register_message_handler(self._handle_message)
		except Exception:
			logger.exception("조건검색 등록 실패")

	async def _cancel_subscription(self):
		"""CNSRCLR로 조건검색 해제."""
		try:
			await self.client.send_websocket({
				'trnm': 'CNSRCLR',
				'seq': str(self.condition_seq),
			})
			self.is_active = False
			logger.info(f"조건검색 해제 (seq={self.condition_seq}), 오늘 누적 {self.match_count}건")
			self.match_count = 0
		except Exception:
			logger.exception("조건검색 해제 실패")

	async def _handle_message(self, msg: dict):
		"""WebSocket 메시지 처리 — CNSRREQ 응답 / REAL 이벤트."""
		trnm = msg.get('trnm')

		# 1. CNSRREQ 응답 = 초기 스냅샷
		if trnm == 'CNSRREQ':
			return_code = msg.get('return_code', -1)
			if return_code != 0:
				logger.warning(f"CNSRREQ failed: {msg.get('return_msg')}")
				return

			data = msg.get('data', [])
			for item in data:
				code = None
				if isinstance(item, dict):
					code = item.get('jmcode') or (item.get('values') or {}).get('9001')
				if not code:
					continue
				code = str(code).lstrip('A').strip()
				if code and len(code) == 6:
					await self._add_to_pool(code, source='snapshot')

			logger.info(f"[조건검색] 초기 스냅샷 {len(data)}건 처리")

		# 2. REAL = 실시간 편입/이탈
		elif trnm == 'REAL':
			for item in msg.get('data', []):
				if not isinstance(item, dict):
					continue
				if item.get('type') != '02':  # 조건검색 실시간 타입
					continue
				values = item.get('values', {}) or {}
				code = str(values.get('9001', '')).lstrip('A').strip()
				event = values.get('843', '')  # 'I' 편입, 'D' 이탈

				if event == 'I' and code and len(code) == 6:
					await self._add_to_pool(code, source='realtime')
				elif event == 'D':
					pass  # 이탈 무시 — 그날 한 번이라도 매칭되면 후보

	async def _add_to_pool(self, code: str, source: str):
		"""collection_pool에 종목 추가. source는 seq_id 슬롯에 기록."""
		try:
			await add_to_pool(code, seq_id=f"{self.condition_seq}:{source}")
			self.match_count += 1
			if self.match_count % 10 == 0:
				logger.info(f"[수집] 누적 {self.match_count}건 (최근: {code})")
		except Exception:
			logger.exception(f"add_to_pool 실패: {code}")
