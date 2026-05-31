"""
장중 차단 종목 재진입 감시 매수.

09:00 buy_executor가 갭상승(+5%)/갭하락(-3%) 차단된 종목은 watching 큐로 이동.
WatchingBuyer가 5분 간격으로 ka10001 호출 → 정상 범위 30분 유지 시 매수.

흐름:
  09:05/09:10/.../14:25  종목별 ka10001 → 정상 진입 판정
    - gap_up 차단: cur_price / prev_close < 1.05 → 정상
    - gap_down 차단: cur_price / prev_close > 0.97 → 정상
  정상 첫 진입 → normal_since 기록 + 알림
  cur_time - normal_since ≥ 30분 → 매수 실행
  정상 → 차단 범위 이탈 → normal_since=None 리셋 + 알림
  14:30 → 잔류 종목 전체 제거 + 요약 알림

매수 직전 추가 방어 (09:00과 동일):
  halt / pnl 한도 / holdings 중복 / 일일 5건 한도(EOD+watching 합산)

매수 실패:
  같은 rc 3회 연속 → 폐기 (consecutive_failed_count)
  다른 rc → 카운트 1로 리셋

영구 원칙: 봇 데몬 내부에서만 watching 큐 조작.
"""
import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 정상 진입 임계값 (buy_executor와 동일)
GAP_UP_LIMIT = 1.05
GAP_DOWN_LIMIT = 0.97
LIMIT_PRICE_UPPER = 1.02

# 운영 상수
HOLD_MINUTES = 30                  # 정상 범위 유지 시간 (분)
POLL_INTERVAL_MIN = 5              # ka10001 polling 간격 (분)
LOOP_SLEEP_SEC = 30                # scheduler loop sleep (초)
START_TIME = time(9, 5)            # 09:05 시작 (09:00 매수 + 차단 처리 완료 후)
END_TIME = time(14, 30)            # 14:30 종료
MAX_FAILED_RETRIES = 3             # 같은 rc 연속 실패 한도

ORDER_QTY = 1
STOP_LOSS_PCT = -0.03


# ─────────────────────────────────────────────────────────
# Pure helpers (단위 테스트용 — 외부 의존 X)
# ─────────────────────────────────────────────────────────
def is_normal_range(block_reason: str, ratio: float) -> bool:
	"""정상 범위 진입 판정.

	gap_up 차단이었으면 ratio < GAP_UP_LIMIT(1.05)
	gap_down 차단이었으면 ratio > GAP_DOWN_LIMIT(0.97)
	"""
	if block_reason == 'blocked_gap_up':
		return ratio < GAP_UP_LIMIT
	if block_reason == 'blocked_gap_down':
		return ratio > GAP_DOWN_LIMIT
	return False


def hold_elapsed_minutes(normal_since_iso, now: datetime) -> float:
	"""정상 진입 후 경과 분."""
	if not normal_since_iso:
		return 0.0
	try:
		t = datetime.fromisoformat(normal_since_iso)
	except (TypeError, ValueError):
		return 0.0
	return (now - t).total_seconds() / 60.0


def calc_failure_state(entry: dict, rc) -> tuple:
	"""매수 실패 후 (new_count, new_rc, should_discard) 반환.

	같은 rc 연속 3회 → discard=True.
	다른 rc → count=1로 리셋.
	"""
	last_rc = entry.get('last_failed_rc')
	count = entry.get('consecutive_failed_count', 0) or 0
	if rc == last_rc:
		count += 1
	else:
		count = 1
		last_rc = rc
	should_discard = count >= MAX_FAILED_RETRIES
	return count, last_rc, should_discard


class WatchingBuyer:
	"""차단 종목 장중 감시 + 30분 유지 시 매수."""

	def __init__(self, bot_ref):
		"""bot_ref: ChatCommand 인스턴스 (token_manager / buy_executor MAX_BUYS_PER_DAY 등 접근용)."""
		self.bot = bot_ref
		self._task: Optional[asyncio.Task] = None
		self._last_poll_min: Optional[int] = None  # 마지막 polling minute (중복 방지)
		self._end_announced_today = False

	def start(self):
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._scheduler_loop())
			logger.info("WatchingBuyer started")

	def stop(self):
		if self._task and not self._task.done():
			self._task.cancel()

	async def _scheduler_loop(self):
		"""30초마다 시간 체크. 5분 경계 + 09:05~14:30 사이면 poll, 14:30 도달 시 종료."""
		while True:
			try:
				now = datetime.now()
				cur_t = now.time()

				# 자정 0시대 → end_announced 리셋
				if now.hour == 0:
					self._end_announced_today = False
					self._last_poll_min = None

				# 14:30 도달 시 종료 처리 (한 번만)
				if cur_t >= END_TIME and not self._end_announced_today:
					await self._handle_end_of_day()
					self._end_announced_today = True

				# 운영 시간(09:05 ~ 14:30) + 5분 경계
				elif START_TIME <= cur_t < END_TIME:
					if now.minute % POLL_INTERVAL_MIN == 0 and self._last_poll_min != now.minute:
						self._last_poll_min = now.minute
						await self._poll_all()

				await asyncio.sleep(LOOP_SLEEP_SEC)
			except asyncio.CancelledError:
				raise
			except Exception:
				logger.exception("[watching_buyer] loop error")
				await asyncio.sleep(60)

	async def _poll_all(self):
		"""watching 전 종목에 대해 ka10001 호출 + 정상 진입/이탈/30분 유지 판정."""
		from utils.buy_queue_watching import load_watching

		watching = await load_watching()
		if not watching:
			return

		logger.info(f"[watching_buyer] poll {len(watching)}종목")
		for entry in watching:
			try:
				await self._poll_one(entry)
			except Exception:
				logger.exception(f"[watching_buyer] poll 실패 {entry.get('code')}")

	async def _poll_one(self, entry: dict):
		"""단일 종목 polling. 정상 진입 → 30분 유지 시 매수."""
		from api.stock_info import fn_ka10001
		from telegram.tel_send import tel_send
		from utils.buy_queue_watching import update_watching

		code = entry['code']
		prev_close = entry.get('prev_close')
		block_reason = entry.get('block_reason')
		if not prev_close or not block_reason:
			logger.warning(f"[watching_buyer] {code} entry 불완전 (prev/reason 없음)")
			return

		token = await self.bot.token_manager.get_token()
		info = await fn_ka10001(code, token=token, silent=True)
		if not isinstance(info, dict):
			return
		cur_price = float(info.get('cur_prc') or 0)
		if cur_price <= 0:
			return

		ratio = cur_price / prev_close
		now_iso = datetime.now().isoformat(timespec='seconds')

		# 정상 범위 판정 (pure helper)
		is_normal = is_normal_range(block_reason, ratio)
		normal_since_iso = entry.get('normal_since')

		if is_normal:
			if normal_since_iso is None:
				# 정상 진입 첫 감지 → normal_since 기록 + 알림
				await update_watching(code, normal_since=now_iso, last_check_at=now_iso)
				pct = (ratio - 1) * 100
				await tel_send(f"👀 {code} 정상 범위 진입 ({pct:+.1f}%) — {HOLD_MINUTES}분 후 매수 예정")
				logger.info(f"[watching_buyer] {code} 정상 진입 (ratio={ratio:.4f})")
				return

			# 30분 유지 판정 (pure helper)
			if hold_elapsed_minutes(normal_since_iso, datetime.now()) >= HOLD_MINUTES:
				await self._try_buy(code, cur_price, prev_close, entry)
				# 매수 처리 후 update_watching은 _try_buy 내부에서 (성공/실패별 분기)
			else:
				await update_watching(code, last_check_at=now_iso)
		else:
			# 이탈
			if normal_since_iso is not None:
				# 정상 → 차단 범위 이탈, 타이머 리셋
				await update_watching(code, normal_since=None, last_check_at=now_iso)
				pct = (ratio - 1) * 100
				await tel_send(f"↩️ {code} 다시 차단 범위 진입 ({pct:+.1f}%) — 타이머 리셋")
				logger.info(f"[watching_buyer] {code} 이탈 (ratio={ratio:.4f})")
			else:
				# 처음부터 차단 상태 — 단순 last_check_at 갱신
				await update_watching(code, last_check_at=now_iso)

	async def _try_buy(self, code: str, cur_price: float, prev_close: float, entry: dict):
		"""30분 유지된 종목 매수 시도. 09:00과 동일 방어 (halt/pnl/holdings/한도).

		매수 실패 시 같은 rc 3회 연속이면 폐기, 다른 rc면 카운트 리셋.
		"""
		from telegram.tel_send import tel_send
		from utils.holdings import load_holdings, add_holding, calc_sell_deadline
		from utils.buy_queue_watching import update_watching, remove_from_watching
		from utils.pnl_tracker import check_limits
		from api.buy_stock import fn_kt10000

		# 1. halt 체크
		if getattr(self.bot, 'is_halted', False):
			logger.info(f"[watching_buyer] {code} halt 상태 — 매수 스킵")
			return

		# 2. 보유 중복 (pending_fill / filled) — stick은 우회 (매일 누적 매수)
		source = entry.get('source', 'pick')
		holdings = await load_holdings()
		held_codes = {h['code'] for h in holdings if h.get('status') in ('pending_fill', 'filled')}
		if code in held_codes and source != 'stick':
			logger.warning(f"[watching_buyer] {code} 이미 보유 중 — watching 제거")
			await remove_from_watching(code)
			await tel_send(f"⏸️ {code} watching 매수 직전 이미 보유 중 — 감시 종료")
			return

		# 3. 일일 5건 한도 (EOD + watching 합산: filled + pending_fill 카운트)
		from modules.buy_executor import MAX_BUYS_PER_DAY
		today = datetime.now().strftime('%Y-%m-%d')
		today_buys = [h for h in holdings
		              if h.get('buy_date') == today
		              and h.get('status') in ('pending_fill', 'filled')]
		if len(today_buys) >= MAX_BUYS_PER_DAY:
			logger.info(f"[watching_buyer] {code} 일일 한도 {MAX_BUYS_PER_DAY}건 도달 — 매수 스킵")
			return

		# 4. pnl 한도
		try:
			holdings_filled = [h for h in holdings if h.get('status') == 'filled']

			async def _get_price(c):
				from api.stock_info import fn_ka10001
				try:
					tok = await self.bot.token_manager.get_token()
					info = await fn_ka10001(c, token=tok, silent=True)
					if isinstance(info, dict):
						return int(float(info.get('cur_prc') or 0))
				except Exception:
					return 0
				return 0

			limit = await check_limits(holdings_filled, _get_price)
			if limit:
				self.bot.is_halted = True
				reason_kr = '일일 한도' if limit == 'daily_halt' else '주간 한도'
				logger.warning(f"[watching_buyer] pnl {reason_kr} 도달 — 매수 스킵 (halt 자동)")
				await tel_send(f"⚠️ [watching] {reason_kr} 도달 — 자동 매수 정지 (halt)")
				return
		except Exception:
			logger.exception("[watching_buyer] pnl 한도 체크 실패 (매수 진행)")

		# 5. 매수 실행 — ka10004 호가(bid) 직접 사용 (호가 단위 위반 불가, chk_n_buy 패턴)
		from api.check_bid import fn_ka10004 as check_bid
		try:
			bid = int(await check_bid(code, token=await self.bot.token_manager.get_token()))
		except Exception:
			logger.exception(f"[watching_buyer] {code} 호가 조회 실패")
			await self._record_failure(code, entry, 'bid_query_failed')
			return
		if bid <= 0:
			logger.warning(f"[watching_buyer] {code} 호가 응답 0 — 매수 스킵")
			return

		limit_price = bid
		qty = int(entry.get('qty', 1) or 1)
		token = await self.bot.token_manager.get_token()
		try:
			return_code, ord_no = await fn_kt10000(
				stk_cd=code,
				ord_qty=qty,
				ord_uv=limit_price,
				token=token,
				order_type='limit',
				skip_timeout=True,
			)
		except Exception as e:
			logger.exception(f"[watching_buyer] {code} 매수 호출 예외")
			await self._record_failure(code, entry, 'exception')
			return

		if return_code != 0:
			await self._record_failure(code, entry, return_code)
			return

		# 매수 성공 — holdings 추가 + watching 제거 + 0B 등록 + 알림
		holding_entry = {
			'code': code,
			'buy_price': limit_price,
			'buy_qty': qty,
			'buy_date': today,
			'buy_datetime': datetime.now().isoformat(timespec='seconds'),
			'ord_no': str(ord_no) if ord_no else '',
			'stop_loss_price': int(limit_price * (1 + STOP_LOSS_PCT)),
			'sell_deadline': calc_sell_deadline(today),
			'status': 'pending_fill',
			'source': source,
		}
		if entry.get('tpr') is not None:
			holding_entry['tpr'] = float(entry['tpr'])
		if entry.get('slr') is not None:
			holding_entry['slr'] = float(entry['slr'])
		await add_holding(holding_entry)
		await remove_from_watching(code)
		try:
			ws = getattr(self.bot, 'websocket', None)
			if ws is not None and hasattr(ws, '_queue_reg_request'):
				await ws._queue_reg_request([code], ['0B'], force_refresh=False)
		except Exception:
			logger.exception(f"[watching_buyer] {code} 0B 등록 실패")

		hhmm = datetime.now().strftime('%H:%M')
		await tel_send(f"✅ {code} 감시 매수 완료 @ {limit_price:,}원 ({qty}주, {hhmm})")
		logger.info(f"[watching_buyer] {code} 매수 성공 @ {limit_price}")

	async def _record_failure(self, code: str, entry: dict, rc):
		"""매수 실패 카운트. 같은 rc 3회 연속이면 폐기."""
		from telegram.tel_send import tel_send
		from utils.buy_queue_watching import update_watching, remove_from_watching

		count, last_rc, should_discard = calc_failure_state(entry, rc)

		if should_discard:
			await remove_from_watching(code)
			await tel_send(f"❌ {code} watching 매수 {MAX_FAILED_RETRIES}회 연속 실패 (rc={rc}) — 감시 종료")
			logger.warning(f"[watching_buyer] {code} 폐기 ({MAX_FAILED_RETRIES}회 연속 rc={rc})")
		else:
			await update_watching(code, last_failed_rc=last_rc, consecutive_failed_count=count)
			logger.info(f"[watching_buyer] {code} 매수 실패 rc={rc} ({count}/{MAX_FAILED_RETRIES}) — 5분 후 재시도")

	async def _handle_end_of_day(self):
		"""14:30 도달 시 watching 잔류 종목 전체 제거 + 요약 알림."""
		from telegram.tel_send import tel_send
		from utils.buy_queue_watching import load_watching, clear_watching
		from api.stock_info import fn_ka10001

		remaining = await load_watching()
		if not remaining:
			return

		# 잔류 종목 마지막 등락률 요약 (best-effort, 실패해도 진행)
		token = None
		try:
			token = await self.bot.token_manager.get_token()
		except Exception:
			pass

		lines = [f"🕐 [14:30] 감시 종료 — 미매수 {len(remaining)}건"]
		for entry in remaining:
			code = entry['code']
			prev_close = entry.get('prev_close')
			pct_str = ""
			try:
				if token and prev_close:
					info = await fn_ka10001(code, token=token, silent=True)
					if isinstance(info, dict):
						cur = float(info.get('cur_prc') or 0)
						if cur > 0:
							pct = (cur / prev_close - 1) * 100
							pct_str = f" (마지막 {pct:+.1f}%, 정상 진입 못함)"
			except Exception:
				pass
			lines.append(f"- {code}{pct_str}")
		lines.append("\n익일 재pick 필요 시 텔레그램 명령으로 등록.")
		await tel_send("\n".join(lines))

		cleared = await clear_watching()
		logger.info(f"[watching_buyer] 14:30 종료 — watching {cleared}건 제거")
