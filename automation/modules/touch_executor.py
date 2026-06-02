"""
touch_executor — 일중 최저점 반등 매수.

명령: touch <code> [수량]
트리거: cur >= low + (touch_rate%/100) × (open - low)
- 시가/저가/현재가는 ka10001 raw에서 추출
- 매수 시간 무관 (장 중 09:00 ~ 15:20)
- 시장가 매수 (auction과 동일 패턴)
- 매수 후 큐에서 제거 (1회 종료) — Lee 6/2 결정
- holdings source='touch' — Feature 2 + 15:20 청산 (stick_executor filter에 포함)

settings: touch_rate (% 단위, 기본 10)
"""
import asyncio
import logging
from datetime import datetime, date, time
from typing import Optional

logger = logging.getLogger(__name__)

MARKET_OPEN = time(9, 0)
CLOSING_AUCTION = time(15, 20)
POLL_INTERVAL = 30  # 초


def _abs_int(raw) -> float:
	"""키움 음수 표기('-1234') 절댓값 → float."""
	if isinstance(raw, str) and raw.startswith('-'):
		raw = raw[1:]
	try:
		return float(raw) if raw else 0.0
	except (ValueError, TypeError):
		return 0.0


class TouchExecutor:
	def __init__(self, bot_ref):
		self.bot = bot_ref
		self._task: Optional[asyncio.Task] = None

	def start(self):
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._scheduler_loop())
			logger.info("TouchExecutor started")

	def stop(self):
		if self._task and not self._task.done():
			self._task.cancel()

	async def _scheduler_loop(self):
		while True:
			try:
				now = datetime.now().time()
				if MARKET_OPEN <= now < CLOSING_AUCTION:
					await self._check_touches()
			except asyncio.CancelledError:
				return
			except Exception:
				logger.exception("[touch] scheduler 예외")
			await asyncio.sleep(POLL_INTERVAL)

	async def _check_touches(self):
		# 장 외에는 즉시 return (등록 명령에서 직접 호출되는 경우 안전망)
		now = datetime.now().time()
		if not (MARKET_OPEN <= now < CLOSING_AUCTION):
			return

		from telegram.tel_send import tel_send
		from utils.buy_queue import load_queue, remove_from_queue
		from utils.holdings import add_holding, calc_sell_deadline
		from utils.blocklist_checker import is_blocked
		from utils.sold_stocks_manager import is_in_cooldown
		from utils.get_setting import get_setting
		from api.stock_info import fn_ka10001
		from api.buy_stock import fn_kt10000

		queue = await load_queue()
		touches = [q for q in queue if q.get('source') == 'touch']
		if not touches:
			return

		rate = float(get_setting('touch_rate', 10.0))
		cooldown_h = get_setting('sell_cooldown_hours', 24)
		token = await self.bot.token_manager.get_token()
		if not token:
			return

		today = date.today().isoformat()

		for entry in touches:
			code = entry.get('code')
			qty = int(entry.get('qty', 1) or 1)
			tpr = entry.get('tpr')
			slr = entry.get('slr')

			if is_blocked(code):
				continue
			if is_in_cooldown(code, cooldown_h):
				continue

			# ka10001로 시가/저가/현재가
			try:
				info = await fn_ka10001(code, token=token, silent=True)
			except Exception:
				logger.exception(f"[touch] {code} ka10001 실패")
				continue

			cur = float(info.get('cur_prc') or 0)
			raw = info.get('raw', {}) if isinstance(info, dict) else {}
			open_prc = _abs_int(raw.get('open_pric'))
			low = _abs_int(raw.get('low_pric'))

			if cur <= 0 or open_prc <= 0 or low <= 0:
				continue
			# 시가가 그날 최저 = 반등 시작 정의 불가 → 다음 polling 대기
			if open_prc <= low:
				continue

			trigger = low + (rate / 100.0) * (open_prc - low)
			if cur < trigger:
				continue

			# 트리거 충족 → 시장가 매수 (rc=3 자동 재시도)
			rc, ord_no = None, None
			for attempt in range(2):
				try:
					rc, ord_no = await fn_kt10000(
						stk_cd=code, ord_qty=qty, ord_uv=0, token=token,
						order_type='market', skip_timeout=True,
					)
				except Exception:
					logger.exception(f"[touch] {code} 매수 예외 attempt={attempt}")
					rc = 'exc'
					break
				if str(rc) == '3' and attempt == 0:
					logger.warning(f"[touch] {code} rc=3 → 토큰 강제 재발급 후 재시도")
					try:
						token = await self.bot.token_manager.get_token(force_refresh=True)
					except Exception:
						logger.exception("[touch] 토큰 강제 재발급 실패")
						break
					continue
				break

			if rc != 0 and rc != '0':
				await remove_from_queue(code, source='touch')
				await tel_send(f"❌ [touch] {code} 매수 실패 rc={rc} — 큐 제거")
				continue

			# holdings 등록
			holding = {
				'code':             code,
				'buy_price':        0,
				'buy_qty':          qty,
				'buy_date':         today,
				'buy_datetime':     datetime.now().isoformat(timespec='seconds'),
				'ord_no':           str(ord_no) if ord_no else '',
				'sell_deadline':    calc_sell_deadline(today),
				'status':           'pending_fill',
				'source':           'touch',
			}
			if tpr is not None:
				holding['tpr'] = float(tpr)
			if slr is not None:
				holding['slr'] = float(slr)
			await add_holding(holding)
			await remove_from_queue(code, source='touch')

			await tel_send(
				f"🎯 [touch 매수] {code} {qty}주 (시장가)\n"
				f"  시가={int(open_prc):,} 저가={int(low):,} 현재가={int(cur):,}\n"
				f"  트리거가={int(trigger):,} (반등 ≥{rate}%) ord_no {ord_no}"
			)
			logger.info(f"[touch] {code} {qty}주 매수 ord_no={ord_no} cur={cur} trigger={trigger}")

			# 0B 등록
			try:
				ws = getattr(self.bot, 'websocket', None)
				if ws is not None and hasattr(ws, '_queue_reg_request'):
					await ws._queue_reg_request([code], ['0B'], force_refresh=False)
			except Exception:
				logger.exception(f"[touch] {code} 0B 등록 실패")
