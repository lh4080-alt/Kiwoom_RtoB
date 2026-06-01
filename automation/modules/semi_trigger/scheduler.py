"""semi_trigger 자동 스케줄러 — 02:00 + 05:30 KST snapshot 전송.

Lee 6/2 요구: 자동 전송 2회 + 수동 명령. 봇 데몬 내부에서만 작동 (영구 원칙 #30).
"""
import asyncio
import logging
from datetime import datetime, time
from typing import Optional

logger = logging.getLogger(__name__)

# 자동 snapshot 시각
T_MIDNIGHT_START = time(2, 0)    # 02:00 KST
T_MIDNIGHT_END = time(2, 5)
T_US_CLOSE_START = time(5, 30)   # 05:30 KST (서머타임 ET 16:30)
T_US_CLOSE_END = time(5, 35)

LOOP_SLEEP_SEC = 30


class SnapshotScheduler:
	"""02:00 + 05:30 KST 자동 snapshot 트리거."""

	def __init__(self, bot_ref):
		self.bot = bot_ref
		self._task: Optional[asyncio.Task] = None
		self._sent_midnight_today: bool = False
		self._sent_us_close_today: bool = False
		self._last_reset_date: Optional[str] = None

	def start(self):
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._loop())
			logger.info("SnapshotScheduler started")

	def stop(self):
		if self._task and not self._task.done():
			self._task.cancel()

	async def _loop(self):
		while True:
			try:
				now = datetime.now()
				today_iso = now.date().isoformat()

				# 새 날 자정 → 플래그 리셋 (00:00 ~ 01:00 사이)
				if self._last_reset_date != today_iso and now.hour < 2:
					self._sent_midnight_today = False
					self._sent_us_close_today = False
					self._last_reset_date = today_iso

				cur_t = now.time()
				# 02:00 KST snapshot
				if (T_MIDNIGHT_START <= cur_t < T_MIDNIGHT_END
				    and not self._sent_midnight_today):
					self._sent_midnight_today = True
					await self._dispatch_snapshot(label='02:00 KST')

				# 05:30 KST snapshot (미국 ET 16:30 정규장 마감 30분 후)
				if (T_US_CLOSE_START <= cur_t < T_US_CLOSE_END
				    and not self._sent_us_close_today):
					self._sent_us_close_today = True
					await self._dispatch_snapshot(label='05:30 KST (미국 마감 후)')

				await asyncio.sleep(LOOP_SLEEP_SEC)
			except asyncio.CancelledError:
				raise
			except Exception:
				logger.exception("[snapshot_scheduler] loop error")
				await asyncio.sleep(60)

	async def _dispatch_snapshot(self, label: str):
		from .snapshot import take_snapshot, resolve_eval_date

		try:
			token = await self.bot.token_manager.get_token()
		except Exception:
			logger.exception(f"[snapshot] {label} 토큰 실패")
			return
		if not token:
			logger.warning(f"[snapshot] {label} 토큰 None")
			return

		eval_date = resolve_eval_date()
		if not eval_date:
			from telegram.tel_send import tel_send
			await tel_send(
				f"⚠️ [snapshot {label}] daily_factors 비어있음 — "
				"어제 16:00 evening pipeline 실패 추정"
			)
			return

		try:
			await take_snapshot(token=token, eval_date=eval_date, label=label,
			                    send_telegram=True)
		except Exception:
			logger.exception(f"[snapshot] {label} take_snapshot 실패")
