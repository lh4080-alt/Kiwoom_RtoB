"""
touch_executor — 일중 최저점 반등 매수 (0B 실시간 + 5분 fallback polling).

명령: touch <code> [수량]
트리거: cur >= low + (touch_rate%/100) × (open - low)
- 0B WebSocket 푸시로 현재가 받음 (실시간)
- 시가는 09:00 형성 후 캐시 (영구)
- 저가는 push current_price가 더 낮으면 자동 갱신
- 캐시 미존재 시 ka10001 1회 호출 (시가 0인 09:00 전 등록 보호)
- 매수 시간 무관 (장 중 09:00 ~ 15:20)
- 시장가 매수 (auction과 동일 패턴)
- 매수 후 큐에서 제거 (1회 종료)
- holdings source='touch' — Feature 2 + 15:20 청산

settings: touch_rate (% 단위, 기본 10)
"""
import asyncio
import logging
from datetime import datetime, date, time
from typing import Optional

logger = logging.getLogger(__name__)

MARKET_OPEN = time(9, 0)
CLOSING_AUCTION = time(15, 20)
FALLBACK_POLL_INTERVAL = 300  # 5분 — 0B 누락 케이스 대비


def _abs_int(raw) -> float:
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
		# 동시 호출 직렬화 (race로 중복 매수 방지)
		self._check_lock = asyncio.Lock()
		# 시가/저가 캐시 — {code: {'open': float, 'low': float}}
		self._cache: dict = {}

	def start(self):
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._scheduler_loop())
			# 봇 시작 시 큐 잔존 touch 종목 0B 등록 (장 시작 전 등록한 종목 보호)
			asyncio.create_task(self._reregister_queue_on_startup())
			logger.info("TouchExecutor started (0B push + 5min fallback)")

	def stop(self):
		if self._task and not self._task.done():
			self._task.cancel()

	async def _reregister_queue_on_startup(self):
		"""봇 startup 시 1회 — 큐의 touch 종목 0B 재등록."""
		from utils.buy_queue import load_queue
		try:
			await asyncio.sleep(3)  # WebSocket 연결 대기
			queue = await load_queue()
			codes = [q.get('code') for q in queue if q.get('source') == 'touch']
			if not codes:
				return
			await self._register_0b(codes)
			logger.info(f"[touch] 봇 startup 큐 0B 재등록: {codes}")
		except Exception:
			logger.exception("[touch] startup 0B 재등록 실패")

	async def _register_0b(self, codes: list):
		"""WebSocket 0B 등록 (등록 후 키움이 호가 변동 시 push)."""
		try:
			ws = getattr(self.bot, 'websocket', None)
			if ws is None or not hasattr(ws, '_queue_reg_request'):
				return
			await ws._queue_reg_request(codes, ['0B'], force_refresh=False)
			logger.info(f"[touch] 0B 등록 요청: {codes}")
		except Exception:
			logger.exception(f"[touch] 0B 등록 실패: {codes}")

	async def register_for_touch(self, code: str):
		"""touch 등록 명령에서 호출 — 0B 등록 + 캐시 무효화."""
		self._cache.pop(code, None)  # 새 등록이면 캐시 무효화 (새로 받아옴)
		await self._register_0b([code])

	async def _scheduler_loop(self):
		"""Fallback polling — 0B 누락 시나리오 대비 (5분 주기)."""
		while True:
			try:
				now = datetime.now().time()
				if MARKET_OPEN <= now < CLOSING_AUCTION:
					await self._check_touches()
			except asyncio.CancelledError:
				return
			except Exception:
				logger.exception("[touch] scheduler 예외")
			await asyncio.sleep(FALLBACK_POLL_INTERVAL)

	async def on_0b_quote(self, code: str, current_price: float):
		"""0B push 핸들러 — 호가 변동마다 호출. 트리거 검증."""
		now = datetime.now().time()
		if not (MARKET_OPEN <= now < CLOSING_AUCTION):
			return
		# 동시 호출 직렬화
		if self._check_lock.locked():
			return
		async with self._check_lock:
			await self._evaluate_one(code, current_price_hint=current_price)

	async def _check_touches(self):
		"""Fallback polling 본체 (5분 주기). 큐 전체 검증."""
		now = datetime.now().time()
		if not (MARKET_OPEN <= now < CLOSING_AUCTION):
			return
		if self._check_lock.locked():
			return
		async with self._check_lock:
			from utils.buy_queue import load_queue
			queue = await load_queue()
			touches = [q for q in queue if q.get('source') == 'touch']
			for entry in touches:
				await self._evaluate_one(entry.get('code'), current_price_hint=None)

	async def _evaluate_one(self, code: str, current_price_hint=None):
		"""한 종목 트리거 검증 + 충족 시 매수.

		current_price_hint: 0B push의 현재가 (있으면 ka10001 안 부르고 사용).
		"""
		from telegram.tel_send import tel_send
		from utils.buy_queue import load_queue, remove_from_queue
		from utils.holdings import add_holding, calc_sell_deadline
		from utils.blocklist_checker import is_blocked
		from utils.sold_stocks_manager import is_in_cooldown
		from utils.get_setting import get_setting
		from api.stock_info import fn_ka10001
		from api.buy_stock import fn_kt10000

		if not code:
			return

		# 큐에 그 종목 touch 항목 있는지 확인
		queue = await load_queue()
		entry = next((q for q in queue if q.get('code') == code and q.get('source') == 'touch'), None)
		if not entry:
			return

		qty = int(entry.get('qty', 1) or 1)
		tpr = entry.get('tpr')
		slr = entry.get('slr')

		if is_blocked(code):
			return
		cooldown_h = get_setting('sell_cooldown_hours', 24)
		if is_in_cooldown(code, cooldown_h):
			return

		# 시가/저가 캐시 — 없거나 시가 0이면 ka10001 호출
		cache = self._cache.get(code)
		need_refresh = cache is None or cache.get('open', 0) <= 0 or cache.get('low', 0) <= 0
		token = await self.bot.token_manager.get_token()
		if not token:
			return

		if need_refresh:
			try:
				info = await fn_ka10001(code, token=token, silent=True)
			except Exception:
				logger.exception(f"[touch] {code} ka10001 실패")
				return
			raw = info.get('raw', {}) if isinstance(info, dict) else {}
			open_prc = _abs_int(raw.get('open_pric'))
			low = _abs_int(raw.get('low_pric'))
			cur_from_api = float(info.get('cur_prc') or 0)
			if open_prc <= 0 or low <= 0:
				return  # 시가 아직 형성 안 됨 (장 시작 전 또는 첫 체결 전)
			self._cache[code] = {'open': open_prc, 'low': low}
			cur = cur_from_api if current_price_hint is None else float(current_price_hint)
		else:
			open_prc = cache['open']
			low = cache['low']
			cur = float(current_price_hint) if current_price_hint is not None else 0.0
			if cur <= 0:
				# fallback polling 등 push 없는 경로 → ka10001로 현재가 조회
				try:
					info = await fn_ka10001(code, token=token, silent=True)
					cur = float(info.get('cur_prc') or 0)
				except Exception:
					return
				if cur <= 0:
					return

		# 저가 갱신 (push current_price가 캐시 저가보다 낮으면)
		if cur < low:
			low = cur
			self._cache[code]['low'] = low

		# 시가 ≤ 저가 = 반등 정의 불가 (아직 저점 안 만들어짐)
		if open_prc <= low:
			return

		rate = float(get_setting('touch_rate', 10.0))
		trigger = low + (rate / 100.0) * (open_prc - low)
		if cur < trigger:
			return

		# 매수 시도 (rc=3 자동 재시도)
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
			return

		today = date.today().isoformat()
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

		# Feature 2가 holdings 종목을 0B로 재등록 (그대로 두면 됨 — touch 등록 0B와 같은 채널)
