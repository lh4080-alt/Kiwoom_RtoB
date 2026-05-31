"""
stick 매일 반복 매수 + 동시호가 매도.

흐름:
  08:30~08:45  pre-market 체크 — SOX + NQ 등락률 조회 (yfinance)
               둘 다 ≥ MIN_RISE_PCT → stick 종목들 buy_queue에 추가 (source='stick')
               하나라도 미달 → 그날 stick 스킵 + 알림
               yfinance 실패 → 1분 간격 최대 3회 재시도 후 스킵
  09:00       기존 buy_executor가 buy_queue 처리 (8단계 필터, source='stick'은 held 우회)
  15:20       당일 stick 매수 종목 시장가 매도 (동시호가)
  15:25       15:20 실패 시 1회 재시도

영구 원칙 (메모리 #30): 봇 데몬 내부에서만 stick_list / buy_queue 조작.
"""
import asyncio
import logging
from datetime import datetime, time, date
from typing import Optional

logger = logging.getLogger(__name__)

# pre-market 윈도우 (KST)
PRE_OPEN_START = time(8, 30)
PRE_OPEN_END = time(8, 45)
# 동시호가 매도 윈도우
CLOSING_AUCTION_FIRST = time(15, 20)
CLOSING_AUCTION_RETRY = time(15, 25)
CLOSING_AUCTION_END = time(15, 30)

MIN_RISE_PCT = 0.3                 # SOX & NQ 둘 다 +0.3% 이상이어야 매수
MAX_FETCH_RETRIES = 3              # yfinance 실패 시 재시도 횟수
FETCH_RETRY_GAP_SEC = 60           # 재시도 간격 (1분)
LOOP_SLEEP_SEC = 30                # 스케줄러 폴링 간격


# ─────────────────────────────────────────────────────────
# Pure helpers (단위 테스트용)
# ─────────────────────────────────────────────────────────
def evaluate_pre_market(sox_pct, nq_pct, threshold: float = MIN_RISE_PCT) -> dict:
	"""SOX/NQ 등락률 평가.

	Returns: {
	  'fetch_ok': bool,    # 둘 다 None 아닌지
	  'sox_ok': bool,
	  'nq_ok': bool,
	  'pass': bool,        # 매수 진행 조건 (fetch_ok AND sox_ok AND nq_ok)
	}
	"""
	if sox_pct is None or nq_pct is None:
		return {'fetch_ok': False, 'sox_ok': False, 'nq_ok': False, 'pass': False}
	sox_ok = sox_pct >= threshold
	nq_ok = nq_pct >= threshold
	return {
		'fetch_ok': True,
		'sox_ok': sox_ok,
		'nq_ok': nq_ok,
		'pass': sox_ok and nq_ok,
	}


def should_retry_fetch(attempts: int, last_attempt_at, now: datetime,
                       max_retries: int = MAX_FETCH_RETRIES,
                       gap_sec: int = FETCH_RETRY_GAP_SEC) -> bool:
	"""재시도 가능 여부 판정.

	- attempts >= max_retries → False (한도 초과)
	- last_attempt_at None → True (첫 시도)
	- (now - last) >= gap_sec → True
	"""
	if attempts >= max_retries:
		return False
	if last_attempt_at is None:
		return True
	elapsed = (now - last_attempt_at).total_seconds()
	return elapsed >= gap_sec


def filter_stick_today(holdings: list, today_iso: str) -> list:
	"""동시호가 매도 대상 필터 — source=stick AND buy_date=today AND status=filled.

	pending_fill 제외 (09:30 buy_executor가 미체결 자동 취소).
	어제 stick 잔여(buy_date != today)는 Phase 6 잔여 알림에서 처리.
	"""
	return [
		h for h in holdings
		if h.get('source') == 'stick'
		and h.get('buy_date') == today_iso
		and h.get('status') == 'filled'
	]


def filter_stick_leftover(holdings: list, today_iso: str) -> list:
	"""봇 시작 시 stick 잔여 검사 — source=stick AND status=filled AND buy_date < today.

	어제(이전) 15:20/15:25 동시호가 매도 실패 또는 봇 다운으로 못 판 stick.
	Lee 수동 처리 알림 대상.
	"""
	return [
		h for h in holdings
		if h.get('source') == 'stick'
		and h.get('status') == 'filled'
		and h.get('buy_date', '') < today_iso
	]


class StickExecutor:
	"""stick 매일 매수 + 동시호가 매도."""

	def __init__(self, bot_ref):
		self.bot = bot_ref
		self._task: Optional[asyncio.Task] = None
		self._fetched_today: bool = False     # 08:30 체크 완료 플래그
		self._sell_attempted_first: bool = False
		self._sell_attempted_retry: bool = False
		self._fetch_attempts: int = 0
		self._last_fetch_attempt_at: Optional[datetime] = None

	def start(self):
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._scheduler_loop())
			# 봇 시작 시 stick 잔여 1회 검사 (어제 못 판 종목 알림)
			asyncio.create_task(self._check_leftover_on_startup())
			logger.info("StickExecutor started")

	async def _check_leftover_on_startup(self):
		"""봇 startup 시 1회 — 어제 stick 잔여 종목 텔레그램 알림."""
		from telegram.tel_send import tel_send
		from utils.holdings import load_holdings
		try:
			# 다른 init 완료를 잠시 대기 (텔레그램/토큰 준비)
			await asyncio.sleep(3)
			holdings = await load_holdings()
			today_iso = date.today().isoformat()
			leftover = filter_stick_leftover(holdings, today_iso)
			if not leftover:
				return
			lines = [f"⚠️ [stick 잔여 종목] {len(leftover)}건 — Lee 수동 처리 필요"]
			for h in leftover:
				code = h.get('code', '-')
				qty = h.get('buy_qty', '-')
				buy_date = h.get('buy_date', '-')
				buy_price = h.get('buy_price', 0)
				lines.append(f"- {code} {qty}주 (매수 {buy_date} @ {buy_price:,}원)")
			lines.append("어제 동시호가 매도 실패 또는 봇 다운으로 추정. 매도/유지 판단 필요.")
			await tel_send("\n".join(lines))
			logger.warning(f"[stick] 잔여 {len(leftover)}건 — {[h.get('code') for h in leftover]}")
		except Exception:
			logger.exception("[stick] 잔여 검사 실패")

	def stop(self):
		if self._task and not self._task.done():
			self._task.cancel()

	def _reset_daily_flags(self):
		self._fetched_today = False
		self._sell_attempted_first = False
		self._sell_attempted_retry = False
		self._fetch_attempts = 0
		self._last_fetch_attempt_at = None

	async def _scheduler_loop(self):
		while True:
			try:
				now = datetime.now()
				cur_t = now.time()

				# 자정 0시대 — 일일 플래그 리셋
				if now.hour == 0:
					self._reset_daily_flags()

				# 08:30~08:45 pre-market 체크
				if PRE_OPEN_START <= cur_t < PRE_OPEN_END and not self._fetched_today:
					if should_retry_fetch(self._fetch_attempts, self._last_fetch_attempt_at, now):
						await self._do_pre_market_check(now)

				# 15:20 1차 동시호가 매도
				if CLOSING_AUCTION_FIRST <= cur_t < CLOSING_AUCTION_RETRY and not self._sell_attempted_first:
					self._sell_attempted_first = True
					await self._closing_auction_sell(retry=False)

				# 15:25 재시도
				if CLOSING_AUCTION_RETRY <= cur_t < CLOSING_AUCTION_END and not self._sell_attempted_retry:
					self._sell_attempted_retry = True
					await self._closing_auction_sell(retry=True)

				await asyncio.sleep(LOOP_SLEEP_SEC)
			except asyncio.CancelledError:
				raise
			except Exception:
				logger.exception("[stick_executor] loop error")
				await asyncio.sleep(60)

	async def _do_pre_market_check(self, now: datetime):
		"""SOX/NQ 등락률 조회 → 조건 충족 시 stick 종목들 buy_queue 추가."""
		from api.external_index import fetch_sox_nq
		from telegram.tel_send import tel_send
		from utils.stick_list import load_stick
		from utils.buy_queue import add_to_queue, load_queue

		self._fetch_attempts += 1
		self._last_fetch_attempt_at = now
		logger.info(f"[stick] pre-market 체크 시도 {self._fetch_attempts}/{MAX_FETCH_RETRIES}")

		result = await fetch_sox_nq()
		sox = result.get('sox')
		nq = result.get('nq')

		if sox is None or nq is None:
			# 실패 — 재시도 대기 또는 최종 스킵
			if self._fetch_attempts >= MAX_FETCH_RETRIES:
				self._fetched_today = True  # 더 시도 안 함
				await tel_send(
					f"❌ [stick 데이터 실패] yfinance {MAX_FETCH_RETRIES}회 재시도 실패\n"
					f"SOX={sox} / NQ={nq}\n"
					f"오늘 stick 매수 스킵"
				)
				logger.warning(f"[stick] {MAX_FETCH_RETRIES}회 실패 — 스킵")
			else:
				logger.warning(f"[stick] fetch 실패 (sox={sox}, nq={nq}) — {FETCH_RETRY_GAP_SEC}초 후 재시도")
			return

		# fetch 성공 — 조건 평가
		self._fetched_today = True
		sox_ok = sox >= MIN_RISE_PCT
		nq_ok = nq >= MIN_RISE_PCT

		if not (sox_ok and nq_ok):
			fail_msgs = []
			fail_msgs.append(f"SOX {sox:+.2f}% {'✅' if sox_ok else '❌'}")
			fail_msgs.append(f"NQ {nq:+.2f}% {'✅' if nq_ok else '❌'}")
			await tel_send(
				f"⏸️ [stick 조건 미달] (기준 +{MIN_RISE_PCT}%)\n"
				f"  {' / '.join(fail_msgs)}\n"
				f"→ 오늘 stick 매수 스킵"
			)
			logger.info(f"[stick] 조건 미달: sox={sox:.2f}%, nq={nq:.2f}%")
			return

		# 조건 충족 — stick 종목들 buy_queue에 추가
		items = await load_stick()
		if not items:
			await tel_send(
				f"✅ [stick 조건 충족] SOX {sox:+.2f}% / NQ {nq:+.2f}%\n"
				f"⚠️ 등록된 stick 종목 없음"
			)
			logger.info("[stick] stick_list 비어있음")
			return

		added, duplicate = [], []
		for it in items:
			code = it.get('code')
			qty = int(it.get('qty', 1) or 1)
			tpr = it.get('tpr')
			slr = it.get('slr')
			if await add_to_queue(code, approved_by='stick', qty=qty,
			                       source='stick', tpr=tpr, slr=slr):
				added.append((code, qty))
			else:
				duplicate.append(code)

		queue = await load_queue()
		lines = [
			f"✅ [stick 조건 충족] SOX {sox:+.2f}% / NQ {nq:+.2f}%",
			f"stick 매수 대기열 진입: 추가 {len(added)} / 중복 {len(duplicate)}",
		]
		if added:
			lines.append("  • " + ", ".join(f"{c} {q}주" for c, q in added))
		if duplicate:
			lines.append(f"  ♻️ 중복: {', '.join(duplicate)}")
		lines.append(f"📦 매수 대기열 총 {len(queue)}건 — 09:00 자동 매수 예정")
		await tel_send("\n".join(lines))
		logger.info(f"[stick] 매수 대기열 추가: {added}")

	async def _closing_auction_sell(self, retry: bool = False):
		"""당일 stick 매수 종목 시장가 매도 (15:20 / 15:25 단일가 매매).

		- source='stick' AND buy_date==today AND status='filled' 만 대상
		- Feature 2가 이미 익절/손절했으면 holdings에서 사라져서 자연 스킵
		- pending_fill은 제외 (체결 안 됐으면 09:30 buy_executor가 이미 취소)
		"""
		from telegram.tel_send import tel_send
		from utils.holdings import load_holdings, remove_holding
		from utils.pnl_tracker import record_realized
		from api.sell_stock import fn_kt10001
		from api.stock_info import fn_ka10001

		holdings = await load_holdings()
		today_iso = date.today().isoformat()
		stick_today = filter_stick_today(holdings, today_iso)

		if not stick_today:
			if not retry:
				logger.info("[stick] 15:20 매도 대상 stick 종목 없음")
			return

		label = "재시도" if retry else "1차"
		logger.info(f"[stick] 동시호가 매도 {label} — {len(stick_today)}종목")

		token = await self.bot.token_manager.get_token()
		sold, failed = [], []
		for h in stick_today:
			code = h['code']
			qty = int(h.get('buy_qty', 0))
			buy_price = int(h.get('buy_price', 0))
			if not code or qty <= 0:
				continue
			try:
				rc, ord_no = await fn_kt10001(
					stk_cd=code, ord_qty=qty, token=token,
					price=0, order_type='market',
				)
				if rc == 0 or rc == '0':
					# 현재가 조회 (PnL 계산용 — best effort)
					cur_price = 0
					try:
						info = await fn_ka10001(code, token=token, silent=True)
						if isinstance(info, dict):
							cur_price = int(float(info.get('cur_prc') or 0))
					except Exception:
						pass
					await remove_holding(code)
					pnl_won = (cur_price - buy_price) * qty if (cur_price and buy_price) else 0
					if pnl_won:
						try:
							await record_realized(pnl_won)
						except Exception:
							logger.exception(f"[stick] {code} pnl 기록 실패")
					sold.append({
						'code': code, 'qty': qty,
						'buy': buy_price, 'sell': cur_price,
						'pnl': pnl_won, 'ord_no': ord_no,
					})
					logger.info(f"[stick] {code} 동시호가 매도 ord_no={ord_no}")
				else:
					failed.append((code, rc))
					logger.warning(f"[stick] {code} 매도 실패 rc={rc}")
			except Exception as e:
				failed.append((code, type(e).__name__))
				logger.exception(f"[stick] {code} 매도 예외")

		# 텔레그램 알림
		header = f"🔚 [stick 동시호가 매도{' 재시도' if retry else ''}]"
		lines = [f"{header} 매도 {len(sold)} / 실패 {len(failed)}"]
		total_pnl = 0
		for s in sold:
			emoji = '🟢' if s['pnl'] > 0 else ('🔵' if s['pnl'] < 0 else '⚪')
			pct = ((s['sell'] - s['buy']) / s['buy'] * 100) if (s['buy'] and s['sell']) else 0
			lines.append(
				f"{emoji} [{s['code']}] {s['qty']}주\n"
				f"   매수 {s['buy']:,} → 매도 {s['sell']:,} ({pct:+.2f}%, {s['pnl']:+,}원)\n"
				f"   ord_no {s['ord_no']}"
			)
			total_pnl += s['pnl']
		if failed:
			lines.append("\n❌ 매도 실패: " + ", ".join(f"{c}(rc={r})" for c, r in failed))
			if not retry:
				lines.append("→ 15:25 1회 재시도 예정")
			else:
				lines.append("⚠️ 재시도 실패 — Lee 수동 처리 필요")
		if sold:
			lines.append(f"\n📋 stick 매도 합계: {total_pnl:+,}원")
		await tel_send("\n".join(lines))
