"""
09:00 자동 매수 실행 (Phase 2 Step C).

흐름:
  09:00 정시 → buy_queue 로드 → halt 체크 → 종목별 시초가/전일종가 조회 →
  갭 차단(±5%/-3%) → 지정가 = min(시초가, 전일종가×1.02) → kt10000 지정가 매수
  → holdings.json에 pending_fill 등록
  09:05 → 잔고 조회로 체결 확인 → filled로 업데이트 (실제 체결가 반영)
  09:30 → 미체결(pending_fill 잔류) 주문 취소

기존 봇 함수 활용 (KiwoomClient.call_api 미사용):
  api.check_bid.fn_ka10004 (호가)
  api.stock_info.fn_ka10001 (현재가/전일종가)
  api.buy_stock.fn_kt10000 (매수)
  api.cancel_order.fn_sc10002 (취소)
  api.acc_val.fn_kt00004 (잔고)
"""
import asyncio
import logging
from datetime import datetime, time
from typing import Optional

logger = logging.getLogger(__name__)

# 다층 방어 상수
GAP_UP_LIMIT = 1.05         # 시초가 / 전일종가 ≥ 1.05 → 차단
GAP_DOWN_LIMIT = 0.97       # 시초가 / 전일종가 ≤ 0.97 → 차단
LIMIT_PRICE_UPPER = 1.02    # 지정가 = min(시초가, 전일종가 × 1.02)
ORDER_QTY = 1
MAX_BUYS_PER_DAY = 5
STOP_LOSS_PCT = -0.03


class BuyExecutor:
	"""09:00 매수 자동 실행."""

	def __init__(self, bot_ref):
		"""bot_ref: ChatCommand 인스턴스. token_manager / telegram 사용."""
		self.bot = bot_ref
		self._executed_today = False
		self._task: Optional[asyncio.Task] = None

	def start(self):
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._scheduler_loop())
			logger.info("BuyExecutor started")

	def stop(self):
		if self._task and not self._task.done():
			self._task.cancel()

	async def _scheduler_loop(self):
		"""09:00 도래 + 자정 리셋 체크."""
		while True:
			try:
				now = datetime.now()
				if now.hour == 0 and self._executed_today:
					self._executed_today = False
					logger.info("[buy_executor] daily flag reset")

				if (now.hour == 9 and now.minute == 0 and now.second < 30
						and not self._executed_today):
					logger.info("[buy_executor] 09:00 트리거")
					await self._execute_at_open()
					self._executed_today = True

				await asyncio.sleep(5)
			except asyncio.CancelledError:
				raise
			except Exception:
				logger.exception("[buy_executor] loop error")
				await asyncio.sleep(30)

	async def _execute_at_open(self):
		"""09:00 매수 실행 본체."""
		from telegram.tel_send import tel_send
		from utils.buy_queue import load_queue, clear_queue
		from utils.holdings import add_holding, calc_sell_deadline

		if getattr(self.bot, 'is_halted', False):
			logger.warning("[buy_executor] halted — 매수 스킵")
			await tel_send("⏸️ [09:00] 매수 정지(halt) 상태 — 자동 매수 스킵")
			return

		queue = await load_queue()
		if not queue:
			await tel_send("[09:00] buy_queue 비어있음 — 매수 없음")
			return

		codes_to_buy = [item['code'] for item in queue[:MAX_BUYS_PER_DAY]]
		logger.info(f"[buy_executor] 매수 대상 {len(codes_to_buy)}건: {codes_to_buy}")

		token = await self.bot.token_manager.get_token()
		if not token:
			await tel_send("❌ [09:00] 토큰 없음 — 매수 불가")
			return

		results = await asyncio.gather(
			*[self._buy_one(code, token) for code in codes_to_buy],
			return_exceptions=True,
		)

		success, failed, blocked = [], [], []
		today = datetime.now().strftime('%Y-%m-%d')
		for code, result in zip(codes_to_buy, results):
			if isinstance(result, Exception):
				failed.append((code, type(result).__name__))
				continue
			status = result.get('status')
			if status == 'ordered':
				success.append(result)
				await add_holding({
					'code': code,
					'buy_price': result['price'],
					'buy_qty': ORDER_QTY,
					'buy_date': today,
					'buy_datetime': datetime.now().isoformat(timespec='seconds'),
					'ord_no': result.get('ord_no', ''),
					'stop_loss_price': int(result['price'] * (1 + STOP_LOSS_PCT)),
					'sell_deadline': calc_sell_deadline(today),
					'status': 'pending_fill',
				})
			elif status and status.startswith('blocked'):
				blocked.append((code, status, result))
			else:
				failed.append((code, status or 'unknown'))

		await clear_queue()

		# 주문 성공 종목들의 0B 실시간 등록 (손절 모니터링용)
		if success:
			try:
				ws = getattr(self.bot, 'websocket', None)
				if ws is not None and hasattr(ws, '_queue_reg_request'):
					await ws._queue_reg_request([r['code'] for r in success], ['0B'], force_refresh=False)
					logger.info(f"[buy_executor] 0B 등록: {[r['code'] for r in success]}")
			except Exception:
				logger.exception("[buy_executor] 0B 등록 실패")

		# 텔레그램 알림
		lines = [f"📦 [09:00 매수 결과] 주문 {len(success)} / 차단 {len(blocked)} / 실패 {len(failed)}"]
		for r in success:
			lines.append(f"  ✅ {r['code']} @ {r['price']:,}원 (ord_no {r.get('ord_no','-')})")
		for code, reason, info in blocked:
			ratio_str = ""
			if 'open' in info and 'prev' in info and info['prev']:
				ratio_str = f" ({info['open']}/{info['prev']} = {info['open']/info['prev']:+.2%})"
			lines.append(f"  ⚠️ {code} {reason}{ratio_str}")
		for code, reason in failed:
			lines.append(f"  ❌ {code} {reason}")
		lines.append("\n09:05 체결 확인 / 09:30 미체결 취소 예정")
		await tel_send("\n".join(lines))

		# 09:05 체결 확인 + 09:30 미체결 취소 태스크
		asyncio.create_task(self._verify_fills_at_0905())
		asyncio.create_task(self._cancel_unfilled_at_0930())

	async def _buy_one(self, code: str, token: str) -> dict:
		"""단일 종목 매수.

		Returns dict with keys: code, status, price (지정가), ord_no, open, prev.
		"""
		try:
			from api.stock_info import fn_ka10001 as stock_info
			from api.buy_stock import fn_kt10000

			info = await stock_info(code, token=token, silent=True)
			if not isinstance(info, dict):
				return {'code': code, 'status': 'failed_no_info'}
			open_or_cur = float(info.get('cur_prc') or 0)   # 09:00 직후엔 시초가에 근접
			prev_close = float(info.get('prev_close_price') or 0)
			if open_or_cur <= 0 or prev_close <= 0:
				return {'code': code, 'status': 'failed_no_price',
				        'open': open_or_cur, 'prev': prev_close}

			ratio = open_or_cur / prev_close
			if ratio >= GAP_UP_LIMIT:
				return {'code': code, 'status': 'blocked_gap_up',
				        'open': int(open_or_cur), 'prev': int(prev_close)}
			if ratio <= GAP_DOWN_LIMIT:
				return {'code': code, 'status': 'blocked_gap_down',
				        'open': int(open_or_cur), 'prev': int(prev_close)}

			limit_price = min(int(open_or_cur), int(prev_close * LIMIT_PRICE_UPPER))

			return_code, ord_no = await fn_kt10000(
				stk_cd=code,
				ord_qty=ORDER_QTY,
				ord_uv=limit_price,
				token=token,
				order_type='limit',
				skip_timeout=True,  # 별도 09:30 취소 흐름 사용
			)
			if return_code != 0:
				return {'code': code, 'status': f'failed_rc={return_code}',
				        'price': limit_price, 'open': int(open_or_cur), 'prev': int(prev_close)}
			return {
				'code': code,
				'status': 'ordered',
				'price': limit_price,
				'ord_no': str(ord_no) if ord_no else '',
				'open': int(open_or_cur),
				'prev': int(prev_close),
			}
		except Exception:
			logger.exception(f"[buy_one] {code} 실패")
			raise

	async def _verify_fills_at_0905(self):
		"""09:05 잔고 조회로 체결 확인 → filled status 업데이트."""
		while True:
			now = datetime.now().time()
			if now >= time(9, 5):
				break
			await asyncio.sleep(10)

		try:
			from api.acc_val import fn_kt00004
			from utils.holdings import load_holdings, save_holdings

			holdings = await load_holdings()
			pending = [h for h in holdings if h.get('status') == 'pending_fill']
			if not pending:
				return

			token = await self.bot.token_manager.get_token()
			balance_rows = await fn_kt00004(print_df=False, token=token)
			# balance_rows: [{stk_cd, stk_nm, pl_rt, rmnd_qty, ...}, ...]
			held_map = {}
			if isinstance(balance_rows, list):
				for row in balance_rows:
					raw_code = str(row.get('stk_cd', '')).lstrip('A')
					if raw_code:
						held_map[raw_code] = row

			updated = []
			for h in holdings:
				if h.get('status') != 'pending_fill':
					updated.append(h)
					continue
				code = h['code']
				if code in held_map:
					row = held_map[code]
					# 평균 매수가 필드 탐색 (실제 키는 운영 데이터로 검증 필요)
					avg_price_raw = (row.get('avg_pchs_pric')
					                 or row.get('avg_prc')
					                 or row.get('pchs_pric')
					                 or h.get('buy_price'))
					try:
						avg_price = int(float(str(avg_price_raw).replace(',', '').lstrip('-+')))
					except (TypeError, ValueError):
						avg_price = h.get('buy_price', 0)
					h['buy_price'] = avg_price or h.get('buy_price', 0)
					h['stop_loss_price'] = int(h['buy_price'] * (1 + STOP_LOSS_PCT))
					h['status'] = 'filled'
					logger.info(f"[buy_fill] {code} 체결 @ {h['buy_price']:,}")
				updated.append(h)
			await save_holdings(updated)
		except Exception:
			logger.exception("[buy_executor] 체결 확인 실패")

	async def _cancel_unfilled_at_0930(self):
		"""09:30 미체결(pending_fill) 주문 취소."""
		while True:
			now = datetime.now().time()
			if now >= time(9, 30):
				break
			await asyncio.sleep(30)

		try:
			from telegram.tel_send import tel_send
			from api.cancel_order import fn_sc10002
			from utils.holdings import load_holdings, remove_holding

			holdings = await load_holdings()
			pending = [h for h in holdings if h.get('status') == 'pending_fill']
			if not pending:
				return

			token = await self.bot.token_manager.get_token()
			cancelled, failed = [], []
			for h in pending:
				try:
					rc = await fn_sc10002(
						stk_cd=h['code'],
						orgn_ord_no=h.get('ord_no', ''),
						ord_qty=h.get('buy_qty', ORDER_QTY),
						token=token,
					)
					if rc == 0 or rc == '0':
						await remove_holding(h['code'])
						cancelled.append(h['code'])
					else:
						failed.append((h['code'], rc))
				except Exception:
					logger.exception(f"[cancel] {h['code']} 실패")
					failed.append((h['code'], 'exception'))

			lines = [f"🧹 [09:30 미체결 취소] 취소 {len(cancelled)} / 실패 {len(failed)}"]
			if cancelled:
				lines.append("  취소: " + ", ".join(cancelled))
			if failed:
				lines.append("  실패: " + ", ".join(f"{c}({rc})" for c, rc in failed))
			await tel_send("\n".join(lines))
		except Exception:
			logger.exception("[buy_executor] 미체결 취소 실패")
