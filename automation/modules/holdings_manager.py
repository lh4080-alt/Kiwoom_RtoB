"""
새 봇 보유 종목 자동 매도 (Phase 2 Step C).

트리거:
  - 실시간 0B push로 손절 -3% 도달
  - 1분 주기로 매도 시한(영업일 5일) 체크
  - 5분 주기로 일일/주간 손실 한도 체크 → 도달 시 is_halted = True

매도: kt10001 시장가.
모든 매도 후 holdings.json에서 제거 + pnl_tracker에 실현 손익 기록.

영구 원칙 (메모리 #30): 봇 데몬 내부에서만 조작.
"""
import asyncio
import logging
from datetime import datetime, date, time
from typing import Optional

logger = logging.getLogger(__name__)

STOP_LOSS_PCT = -0.03   # -3% 손절 (buy_executor와 동기)


class HoldingsManager:
	"""보유 종목 자동 매도."""

	def __init__(self, bot_ref):
		"""bot_ref: ChatCommand 인스턴스."""
		self.bot = bot_ref
		self._task: Optional[asyncio.Task] = None
		self._loss_check_task: Optional[asyncio.Task] = None
		self._notified_daily_halt = False
		self._notified_weekly_halt = False
		# 동시 매도 중복 방지 (0B와 schedule 동시 트리거 케이스)
		self._selling_codes: set = set()

	def start(self):
		"""봇 startup 시 호출. 모니터링 task 시작."""
		asyncio.create_task(self._register_existing_holdings_0b())
		from utils.get_setting import get_setting
		if not get_setting('holdings_manager_enabled', True):
			logger.info("HoldingsManager 비활성 (settings: holdings_manager_enabled=false)")
			return
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._deadline_loop())
		if self._loss_check_task is None or self._loss_check_task.done():
			self._loss_check_task = asyncio.create_task(self._loss_limit_loop())
		logger.info("HoldingsManager started")

	def stop(self):
		for t in (self._task, self._loss_check_task):
			if t and not t.done():
				t.cancel()

	# ─────────────────────────────────────────────────────────
	# 0B 손절 — websocket의 _handle_stock_quote에서 호출
	# ─────────────────────────────────────────────────────────
	async def on_0b_quote(self, code: str, current_price: int):
		"""0B push 수신 시 호출. 손절선 도달 시 시장가 매도."""
		if not code or not current_price:
			return
		from utils.get_setting import get_setting
		if not get_setting('holdings_manager_enabled', True):
			return
		if code in self._selling_codes:
			return

		try:
			from utils.holdings import load_holdings
			holdings = await load_holdings()
			holding = next((h for h in holdings
			                if h.get('code') == code and h.get('status') == 'filled'), None)
			if not holding:
				return
			# touch source는 touch_executor가 자체 손절/익절 (touch_stop_loss_pct / touch_take_profit_pct) 전담
			if holding.get('source') == 'touch':
				return

			buy_price = int(holding.get('buy_price', 0))
			if buy_price <= 0:
				return
			pnl_pct = (current_price - buy_price) / buy_price
			if pnl_pct <= STOP_LOSS_PCT:
				self._selling_codes.add(code)
				try:
					await self._sell_market(holding, reason='stop_loss', current_price=current_price)
				finally:
					self._selling_codes.discard(code)
		except Exception:
			logger.exception(f"[holdings_mgr] on_0b_quote 처리 실패: {code}")

	# ─────────────────────────────────────────────────────────
	# 시한 도달 매도 (1분 주기)
	# ─────────────────────────────────────────────────────────
	async def _deadline_loop(self):
		while True:
			try:
				now = datetime.now()
				if time(9, 0) <= now.time() <= time(15, 30):
					await self._check_deadline()
				await asyncio.sleep(60)
			except asyncio.CancelledError:
				raise
			except Exception:
				logger.exception("[holdings_mgr] deadline_loop error")
				await asyncio.sleep(60)

	async def _register_existing_holdings_0b(self):
		"""봇 startup 시 holdings.json의 filled 종목들을 0B 실시간 등록."""
		try:
			from utils.holdings import load_holdings
			holdings = await load_holdings()
			codes = [h['code'] for h in holdings
			         if h.get('status') == 'filled' and h.get('code')]
			if not codes:
				return
			ws = getattr(self.bot, 'websocket', None)
			if ws is None or not hasattr(ws, '_queue_reg_request'):
				logger.warning("[holdings_mgr] websocket._queue_reg_request 없음 — 0B 등록 스킵")
				return
			await ws._queue_reg_request(codes, ['0B'], force_refresh=False)
			logger.info(f"[holdings_mgr] 기존 holdings 0B 등록: {codes}")
		except Exception:
			logger.exception("[holdings_mgr] 기존 holdings 0B 등록 실패")

	async def _check_deadline(self):
		from utils.holdings import load_holdings
		holdings = await load_holdings()
		today = date.today().isoformat()
		for h in holdings:
			if h.get('status') != 'filled':
				continue
			if today < h.get('sell_deadline', '9999-12-31'):
				continue
			code = h['code']
			if code in self._selling_codes:
				continue
			self._selling_codes.add(code)
			try:
				current = await self._fetch_current_price(code)
				await self._sell_market(h, reason='time_limit', current_price=current)
			finally:
				self._selling_codes.discard(code)

	# ─────────────────────────────────────────────────────────
	# 손실 한도 체크 (5분 주기)
	# ─────────────────────────────────────────────────────────
	async def _loss_limit_loop(self):
		while True:
			try:
				now = datetime.now()
				if time(9, 0) <= now.time() <= time(15, 30):
					await self._check_loss_limits()
				# 자정 지나면 일일 halt 알림 플래그 리셋
				if now.hour == 0:
					self._notified_daily_halt = False
				await asyncio.sleep(300)
			except asyncio.CancelledError:
				raise
			except Exception:
				logger.exception("[holdings_mgr] loss_limit_loop error")
				await asyncio.sleep(300)

	async def _check_loss_limits(self):
		from telegram.tel_send import tel_send
		from utils.holdings import load_holdings
		from utils.pnl_tracker import check_limits

		holdings = await load_holdings()
		filled = [h for h in holdings if h.get('status') == 'filled']
		if not filled:
			return

		limit = await check_limits(filled, self._fetch_current_price)

		if limit == 'weekly_halt' and not self._notified_weekly_halt:
			self._notified_weekly_halt = True
			self.bot.is_halted = True
			await tel_send(
				"🔴 [주간 한도 도달] 이번 주 손실 -15% 초과\n"
				"매수 전면 중단. 신호 시스템 점검 필요."
			)
		elif limit == 'daily_halt' and not self._notified_daily_halt:
			self._notified_daily_halt = True
			self.bot.is_halted = True
			await tel_send(
				"⚠️ [일일 한도 도달] 오늘 손실 -10% 초과\n"
				"추가 매수 중단. 보유 종목은 손절선/시한까지 유지."
			)

	# ─────────────────────────────────────────────────────────
	# 매도 실행
	# ─────────────────────────────────────────────────────────
	async def _sell_market(self, holding: dict, reason: str, current_price: int = 0):
		"""시장가 매도 + holdings 제거 + 실현 손익 기록 + 텔레그램."""
		from telegram.tel_send import tel_send
		from api.sell_stock import fn_kt10001
		from utils.holdings import remove_holding
		from utils.pnl_tracker import record_realized

		code = holding.get('code', '')
		qty = int(holding.get('buy_qty', 0))
		buy_price = int(holding.get('buy_price', 0))
		if not code or qty <= 0:
			return

		try:
			token = await self.bot.token_manager.get_token()
			rc, ord_no = await fn_kt10001(
				stk_cd=code, ord_qty=qty, token=token, price=0, order_type='market',
			)
			if rc != 0 and rc != '0':
				logger.warning(f"[sell] {code} 매도 실패 rc={rc}")
				await tel_send(f"❌ [매도 실패] {code} 사유={reason} rc={rc}")
				return

			removed = await remove_holding(code)
			pnl_won = (current_price - buy_price) * qty if current_price else 0
			if removed and pnl_won:
				await record_realized(pnl_won)

			pnl_pct = ((current_price - buy_price) / buy_price * 100) if (buy_price and current_price) else 0
			reason_kr = {
				'stop_loss': '손절 -3%',
				'time_limit': '시한 5일 도달',
				'manual': '수동',
			}.get(reason, reason)
			emoji = '🔴' if pnl_won < 0 else '🟢' if pnl_won > 0 else '⚪'

			await tel_send(
				f"{emoji} [매도] {code} {qty}주 @ 시장가\n"
				f"사유: {reason_kr}\n"
				f"매수 {buy_price:,} → 현재 {current_price:,} ({pnl_pct:+.2f}%, {pnl_won:+,}원)\n"
				f"주문번호 {ord_no}"
			)
			logger.info(f"[sell] {code} reason={reason} pnl_pct={pnl_pct:.2f}% pnl_won={pnl_won}")
		except Exception:
			logger.exception(f"[sell] {code} 실패")
			try:
				await tel_send(f"❌ [매도 예외] {code} 사유={reason}")
			except Exception:
				pass

	async def _fetch_current_price(self, code: str) -> int:
		"""ka10001로 현재가 조회. 실패 시 0."""
		try:
			from api.stock_info import fn_ka10001
			token = await self.bot.token_manager.get_token()
			info = await fn_ka10001(code, token=token, silent=True)
			if isinstance(info, dict):
				return int(float(info.get('cur_prc') or 0))
		except Exception:
			logger.exception(f"현재가 조회 실패: {code}")
		return 0
