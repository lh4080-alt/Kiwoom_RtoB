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

# 09:00 트리거 윈도우. 5/26 통합 후 매수가는 ka10004 호가 직접 사용 → 시초가 0 응답 방어
# (PRICE_FETCH_*) 불필요. 트리거 시각만 안전 마진으로 15~50초 유지 (시초가 데이터와 무관하게
# 키움 서버 9시 직후 부하 분산).
OPEN_TRIGGER_SECOND_MIN = 15
OPEN_TRIGGER_SECOND_MAX = 50

# 09:35 잔고-봇 정합성 검증에서 제외할 옛 5종목 (Phase 2 Step C 이전 매수, holdings.json 부재)
LEGACY_HELD_CODES = frozenset({'005380', '005930', '012330', '396500', '445290'})


# ─────────────────────────────────────────────────────────
# Pure helpers (단위 테스트용 — 외부 의존 X)
# ─────────────────────────────────────────────────────────
def should_trigger_at_open(now: datetime, executed_today: bool) -> bool:
	"""09:00:05~09:00:30 사이 첫 폴링에서 한 번만 True. 다른 시각/이미 실행 시 False."""
	return (
		now.hour == 9
		and now.minute == 0
		and OPEN_TRIGGER_SECOND_MIN <= now.second < OPEN_TRIGGER_SECOND_MAX
		and not executed_today
	)


def diff_account_vs_holdings(acc_codes, bot_codes, legacy_codes=LEGACY_HELD_CODES):
	"""09:35 잔고 비교 — (acc만, bot만) 차집합 반환.

	계좌에만 있는 종목 중 옛 5종목은 제외 (handoff: 'Lee 직접 관리, holdings.json 부재').
	봇 holdings에 있고 계좌에 없으면 매수 실패/취소 미반영 등 이상.
	"""
	acc = set(acc_codes)
	bot = set(bot_codes)
	legacy = set(legacy_codes)
	only_in_account = (acc - bot) - legacy
	only_in_bot = bot - acc
	return only_in_account, only_in_bot


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
		"""09:00 도래 + 자정 리셋 체크. 트리거는 09:00:05~09:00:30 (시초가 안정 후)."""
		while True:
			try:
				now = datetime.now()
				if now.hour == 0 and self._executed_today:
					self._executed_today = False
					logger.info("[buy_executor] daily flag reset")

				if should_trigger_at_open(now, self._executed_today):
					logger.info(f"[buy_executor] 09:00 트리거 ({now.strftime('%H:%M:%S')})")
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
		from utils.holdings import add_holding, calc_sell_deadline, load_holdings
		from utils.pnl_tracker import check_limits

		# 0-1. 수동 halt 체크
		if getattr(self.bot, 'is_halted', False):
			logger.warning("[buy_executor] halted — 매수 스킵")
			await tel_send("⏸️ [09:00] 매수 정지(halt) 상태 — 자동 매수 스킵")
			return

		# 0-2. pnl 한도 자동 체크 (봇 재시작 시점 한도 도달 상태 보호)
		try:
			holdings_filled = [h for h in await load_holdings() if h.get('status') == 'filled']

			async def _get_price(code):
				from api.stock_info import fn_ka10001
				try:
					tok = await self.bot.token_manager.get_token()
					info = await fn_ka10001(code, token=tok, silent=True)
					if isinstance(info, dict):
						return int(float(info.get('cur_prc') or 0))
				except Exception:
					return 0
				return 0

			limit = await check_limits(holdings_filled, _get_price)
			if limit:
				self.bot.is_halted = True
				reason_kr = '일일 한도' if limit == 'daily_halt' else '주간 한도'
				logger.warning(f"[buy_executor] pnl {reason_kr} 도달 — 매수 스킵")
				await tel_send(
					f"⚠️ [09:00] {reason_kr} 도달 상태 — 자동 매수 정지\n"
					f"보유 종목은 손절선/시한까지 유지. resume 명령으로 강제 해제 가능 (위험)."
				)
				return
		except Exception:
			logger.exception("[buy_executor] pnl 한도 체크 실패 (매수 진행)")

		queue = await load_queue()
		# auction은 08:30 stick_executor / touch는 장 중 touch_executor가 처리.
		queue = [q for q in queue if q.get('source') not in ('auction', 'touch')]
		if not queue:
			await tel_send("[09:00] buy_queue 비어있음 — 매수 없음")
			return

		codes_to_buy = [item['code'] for item in queue[:MAX_BUYS_PER_DAY]]
		# approved_at 보존 — 차단 시 watching 큐로 이동할 때 사용
		approved_map = {item['code']: item.get('approved_at') for item in queue[:MAX_BUYS_PER_DAY]}
		# 종목별 매수 수량 (pick <코드> <수량>)
		qty_map = {item['code']: int(item.get('qty', 1) or 1) for item in queue[:MAX_BUYS_PER_DAY]}
		# 종목별 source (pick/stick) — stick은 held 필터 우회 + tpr/slr override
		source_map = {item['code']: item.get('source', 'pick') for item in queue[:MAX_BUYS_PER_DAY]}
		tpr_map = {item['code']: item.get('tpr') for item in queue[:MAX_BUYS_PER_DAY]}
		slr_map = {item['code']: item.get('slr') for item in queue[:MAX_BUYS_PER_DAY]}
		logger.info(f"[buy_executor] 매수 대상 {len(codes_to_buy)}건: {codes_to_buy} (qty={qty_map}, src={source_map})")

		token = await self.bot.token_manager.get_token()
		if not token:
			await tel_send("❌ [09:00] 토큰 없음 — 매수 불가")
			return

		results = await asyncio.gather(
			*[self._buy_one(code, token, qty_map.get(code, 1),
			                source_map.get(code, 'pick')) for code in codes_to_buy],
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
				holding_entry = {
					'code': code,
					'buy_price': result['price'],
					'buy_qty': qty_map.get(code, 1),
					'buy_date': today,
					'buy_datetime': datetime.now().isoformat(timespec='seconds'),
					'ord_no': result.get('ord_no', ''),
					'stop_loss_price': int(result['price'] * (1 + STOP_LOSS_PCT)),
					'sell_deadline': calc_sell_deadline(today),
					'status': 'pending_fill',
					'source': source_map.get(code, 'pick'),
				}
				# stick 전용 tpr/slr override (Feature 2가 우선 적용)
				if tpr_map.get(code) is not None:
					holding_entry['tpr'] = float(tpr_map[code])
				if slr_map.get(code) is not None:
					holding_entry['slr'] = float(slr_map[code])
				await add_holding(holding_entry)
			elif status and status.startswith('blocked'):
				blocked.append((code, status, result))
				# 차단 종목 폐기 안 함 — watching 큐로 이동, 장중 5분 polling으로 정상 진입 감시
				try:
					from utils.buy_queue_watching import add_to_watching
					prev_close = result.get('prev')
					open_or_cur = result.get('open')
					ratio = (open_or_cur / prev_close) if (prev_close and open_or_cur) else None
					watching_entry = {
						'code': code,
						'approved_at': approved_map.get(code),
						'qty': qty_map.get(code, 1),
						'source': source_map.get(code, 'pick'),
						'blocked_at': datetime.now().isoformat(timespec='seconds'),
						'block_reason': status,
						'block_ratio': ratio,
						'prev_close': prev_close,
					}
					if tpr_map.get(code) is not None:
						watching_entry['tpr'] = float(tpr_map[code])
					if slr_map.get(code) is not None:
						watching_entry['slr'] = float(slr_map[code])
					await add_to_watching(watching_entry)
					logger.info(f"[buy_executor] {code} watching 추가 ({status}, ratio={ratio})")
				except Exception:
					logger.exception(f"[buy_executor] {code} watching 추가 실패")
			else:
				failed.append((code, status or 'unknown'))

		await clear_queue(source='pick')  # auction/touch 항목 보존

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
			lines.append(f"  ✅ {r['code']} {qty_map.get(r['code'], 1)}주 @ {r['price']:,}원 (ord_no {r.get('ord_no','-')})")
		for code, reason, info in blocked:
			ratio_str = ""
			if 'open' in info and 'prev' in info and info['prev']:
				ratio_str = f" ({info['open']}/{info['prev']} = {info['open']/info['prev']:+.2%})"
			lines.append(f"  ⚠️ {code} {reason}{ratio_str} → 감시 시작")
		for code, reason in failed:
			# 새 reason 코드 한글 매핑
			label = {
				'failed_already_held': '이미 보유 중',
				'failed_unfilled_exists': '같은 종목 미체결 주문 존재',
				'failed_cooldown': '매도 후 쿨다운 중',
				'failed_blacklist': '자동매매 금지 종목',
				'failed_no_base_pric': '전일 종가 응답 없음',
				'failed_no_bid': '호가 응답 0',
				'failed_my_stocks_query': '보유 조회 실패',
				'failed_unfilled_query': '미체결 조회 실패',
				'failed_bid_query': '호가 조회 실패',
			}.get(reason, reason)
			lines.append(f"  ❌ {code} {label}")
		lines.append("\n09:05 체결 확인 / 09:30 미체결 취소 예정")
		await tel_send("\n".join(lines))

		# 09:05 체결 확인 + 09:30 미체결 취소 + 09:35 잔고-봇 정합성 검증
		asyncio.create_task(self._verify_fills_at_0905())
		asyncio.create_task(self._cancel_unfilled_at_0930())
		asyncio.create_task(self._verify_holdings_against_account_at_0935())

	async def _buy_one(self, code: str, token: str, qty: int = 1, source: str = 'pick') -> dict:
		"""단일 종목 매수.

		원본 키움 봇 chk_n_buy 패턴 + 우리 보강(갭/halt/pnl) 통합:
		  1) 안전망 4개 (chk_n_buy): 보유 중복 / 미체결 / 쿨다운 / 자동매매 금지
		  2) 가격: ka10001로 base_pric만 받아 갭 검증 + ka10004 호가(bid)로 매수
		     - ka10004 = 매도 최우선 호가. 호가 단위 위반 불가 → 5/26 rc=20 사고 원천 회피
		     - 시초가 미반영(cur_prc=0)이어도 base_pric만 있으면 갭 검증 가능
		  3) 갭상승/하락 차단 (우리 보강 유지)
		  4) fn_kt10000으로 매수 (ord_uv=bid)

		"이미 보유 중" 필터는 제거됨 (Lee 결정 6/1) — 의도적 추가 매수 허용.
		키움 계좌가 자동으로 가중평균 매입가 산정 + Feature 2 평균가 기준 손절/익절.
		나머지 필터(미체결/쿨다운/자동매매금지/갭)는 그대로 적용.

		Returns dict with keys: code, status, price, ord_no, open, prev.
		"""
		from api.stock_info import fn_ka10001 as stock_info
		from api.check_bid import fn_ka10004 as check_bid
		from api.buy_stock import fn_kt10000
		from api.check_unfilled import fn_ka10075 as check_unfilled
		from utils.sold_stocks_manager import is_in_cooldown
		from utils.blocklist_checker import is_blocked
		from utils.stock_code_normalizer import normalize_stock_code
		from utils.get_setting import get_setting

		try:
			# ── 안전망 1: 자동매매 금지 목록 (watching 큐로 가지 않게 failed_* prefix)
			if is_blocked(code):
				return {'code': code, 'status': 'failed_blacklist'}

			# ── 안전망 2 (제거됨): 이미 보유 중 차단 → 의도적 추가 매수 허용
			#    키움 계좌가 자동 가중평균 매입가, Feature 2 평균가 기준 손절/익절 처리.

			# ── 안전망 3: 미체결 (같은 종목 중복 주문 방지)
			try:
				unfilled = await check_unfilled(stk_cd=code, trde_tp='2', token=token)
				if unfilled:
					if isinstance(unfilled, dict):
						unfilled = [unfilled]
					if isinstance(unfilled, list):
						for o in unfilled:
							if not isinstance(o, dict):
								continue
							ocd = normalize_stock_code(o.get('stk_cd') or o.get('pdno') or '')
							if (not ocd) or (ocd == code):
								return {'code': code, 'status': 'failed_unfilled_exists'}
			except Exception:
				logger.exception(f"[_buy_one] {code} 미체결 조회 실패 (매수 중단)")
				return {'code': code, 'status': 'failed_unfilled_query'}

			# ── 안전망 4: 매도 쿨다운 (재매수 회피)
			cooldown_hours = get_setting('sell_cooldown_hours', 24)
			if is_in_cooldown(code, cooldown_hours):
				return {'code': code, 'status': 'failed_cooldown'}

			# ── 가격 1: ka10001로 base_pric (갭 검증용)
			info = await stock_info(code, token=token, silent=True)
			prev_close = abs(float(info.get('prev_close_price') or 0)) if isinstance(info, dict) else 0.0
			if prev_close <= 0:
				return {'code': code, 'status': 'failed_no_base_pric'}

			# ── 가격 2: ka10004 호가 (실제 매수가, 호가 단위 위반 불가)
			try:
				bid = int(await check_bid(code, token=token))
			except Exception:
				logger.exception(f"[_buy_one] {code} 호가 조회 실패")
				return {'code': code, 'status': 'failed_bid_query'}
			if bid <= 0:
				return {'code': code, 'status': 'failed_no_bid', 'prev': int(prev_close)}

			# ── 갭 차단 (우리 보강 유지) — settings.json 동적 (gap_up, gap_down %)
			gap_up_pct = float(get_setting('gap_up', 5.0))
			gap_down_pct = float(get_setting('gap_down', 3.0))
			gap_up_limit = 1.0 + gap_up_pct / 100.0
			gap_down_limit = 1.0 - gap_down_pct / 100.0
			ratio = bid / prev_close
			if ratio >= gap_up_limit:
				return {'code': code, 'status': 'blocked_gap_up',
				        'open': bid, 'prev': int(prev_close)}
			if ratio <= gap_down_limit:
				return {'code': code, 'status': 'blocked_gap_down',
				        'open': bid, 'prev': int(prev_close)}

			# ── 매수 주문 (ka10004 호가 그대로 → 호가 위반 불가)
			return_code, ord_no = await fn_kt10000(
				stk_cd=code,
				ord_qty=qty,
				ord_uv=bid,
				token=token,
				order_type='limit',
				skip_timeout=True,
			)
			if return_code != 0:
				return {'code': code, 'status': f'failed_rc={return_code}',
				        'price': bid, 'open': bid, 'prev': int(prev_close)}
			return {
				'code': code,
				'status': 'ordered',
				'price': bid,
				'ord_no': str(ord_no) if ord_no else '',
				'open': bid,
				'prev': int(prev_close),
			}
		except Exception:
			logger.exception(f"[_buy_one] {code} 예외")
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

	async def _verify_holdings_against_account_at_0935(self):
		"""09:35 — 계좌 실제 보유(kt00004) vs 봇 holdings 정합성 검증.

		ord_no 추출 실패 같은 사일런트 사고를 잡기 위한 안전망:
		- 봇은 매수 안 했다고 생각하는데 계좌엔 들어가 있는 경우 (kt10000 응답 파싱 실패 시 가능)
		- 봇은 매수했다고 기록했는데 계좌엔 없는 경우 (취소 누락 등)
		옛 5종목(LEGACY_HELD_CODES)은 비교 제외 — handoff 영구 원칙대로 Lee 직접 관리.
		"""
		while True:
			now = datetime.now().time()
			if now >= time(9, 35):
				break
			await asyncio.sleep(30)

		try:
			from telegram.tel_send import tel_send
			from api.acc_val import fn_kt00004
			from utils.holdings import load_holdings

			token = await self.bot.token_manager.get_token()
			balance_rows = await fn_kt00004(print_df=False, token=token)

			acc_codes = set()
			if isinstance(balance_rows, list):
				for row in balance_rows:
					raw_code = str(row.get('stk_cd', '')).lstrip('A').strip()
					qty_raw = row.get('rmnd_qty', 0)
					try:
						qty = int(float(str(qty_raw).replace(',', '')))
					except (ValueError, TypeError):
						qty = 0
					if raw_code and qty > 0:
						acc_codes.add(raw_code)

			holdings = await load_holdings()
			bot_codes = {h['code'] for h in holdings
			             if h.get('status') in ('pending_fill', 'filled')}

			only_in_account, only_in_bot = diff_account_vs_holdings(acc_codes, bot_codes)

			if only_in_account or only_in_bot:
				await tel_send(
					f"⚠️ [09:35 잔고-봇 불일치]\n"
					f"  계좌만: {sorted(only_in_account) or '-'}\n"
					f"  봇만: {sorted(only_in_bot) or '-'}\n"
					f"즉시 수동 확인 필요 (ord_no 추출 실패 / 취소 누락 등 가능)"
				)
				logger.warning(
					f"[buy_executor] 09:35 정합성 실패 — only_in_account={only_in_account}, only_in_bot={only_in_bot}"
				)
			else:
				await tel_send(f"✅ [09:35 잔고-봇 일치] {len(acc_codes)}종목")
				logger.info(f"[buy_executor] 09:35 정합성 OK ({len(acc_codes)}종목)")
		except Exception:
			logger.exception("[buy_executor] 09:35 잔고 검증 실패")
