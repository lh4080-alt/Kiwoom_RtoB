"""
봇 내부 daily task.
매일 16:00 KST에 1회 실행:
  1. (예정) daily_analyzer — 수집풀 종목 자동 분석 + 텔레그램 알림
  2. collection_pool 비우기

영구 원칙: 외부 프로세스 데이터 조작 금지 — 봇 내부에서만 처리.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DAILY_TASK_HOUR = 16
DAILY_TASK_MINUTE = 0
RESET_HOUR = 0  # 자정 0시대에 daily_done_today flag 리셋


class DailyTaskManager:
	"""봇 내부 daily task 스케줄러."""

	def __init__(self, bot_ref):
		"""bot_ref: ChatCommand 또는 봇 인스턴스 (daily_analyzer 호출용으로 향후 사용)."""
		self.bot = bot_ref
		self._daily_done_today = False
		self._task: Optional[asyncio.Task] = None

	def start(self):
		"""봇 startup 시 호출. 30초 주기 스케줄러 task 생성."""
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._scheduler_loop())
			logger.info("DailyTaskManager started")

	def stop(self):
		"""봇 shutdown 시 호출."""
		if self._task and not self._task.done():
			self._task.cancel()
			logger.info("DailyTaskManager stopped")

	async def _scheduler_loop(self):
		"""30초 간격으로 16:00 도래 + 자정 리셋 체크."""
		while True:
			try:
				await self._check_and_run()
			except asyncio.CancelledError:
				logger.info("DailyTaskManager cancelled")
				raise
			except Exception:
				logger.exception("daily scheduler loop error")
			await asyncio.sleep(30)

	async def _check_and_run(self):
		now = datetime.now()

		# 자정 0시대 → 어제 done flag 리셋
		if now.hour == RESET_HOUR and self._daily_done_today:
			self._daily_done_today = False
			logger.info("daily_done flag reset at midnight")
			return

		# 16:00 도달 + 오늘 미실행
		if now.hour == DAILY_TASK_HOUR and now.minute >= DAILY_TASK_MINUTE and not self._daily_done_today:
			logger.info(f"daily task triggered at {now.strftime('%H:%M:%S')}")
			await self._run_daily_task()
			# 성공/실패 무관 True 설정 — 중복 재실행 방지 (실패 시 다음날까지 재시도 안 함)
			self._daily_done_today = True

	async def _run_daily_task(self):
		"""16:00 트리거 — daily_analyzer로 위임.
		daily_analyzer가 자체적으로 수집풀/매수대기열 비우기까지 수행.
		"""
		today = datetime.now().strftime('%Y-%m-%d')
		try:
			analyzer = getattr(self.bot, 'daily_analyzer', None)
			if analyzer is None:
				logger.warning("daily_analyzer 미부착 — 풀 비우기만 수행 (fallback)")
				loop = asyncio.get_event_loop()
				from utils.collection_pool import clear_pool
				cleared = await loop.run_in_executor(None, clear_pool)
				logger.info(f"[fallback] collection_pool cleared: {cleared} entries")
				return

			await analyzer.run()
			logger.info(f"daily task completed for {today}")
		except Exception:
			logger.exception("daily task failed (will not retry until tomorrow)")
