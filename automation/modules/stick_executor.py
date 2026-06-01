"""
stick 매일 반복 매수 + 동시호가 매도.

흐름:
  08:30~08:45  pre-market 체크 — SOX + NVDA + MU 등락률 조회 (yfinance)
               3개 중 2개 이상 ≥ MIN_RISE_PCT → stick 종목들 buy_queue 추가 (다수결)
               2개 미만 상승 → 그날 stick 스킵 + 알림
               yfinance 실패 → 1분 간격 최대 3회 재시도 후 스킵
               * SK하이닉스 등 메모리/HBM 종목 매수 신호 3종 다수결:
                 SOX (섹터), NVDA (HBM 수요), MU (DRAM/NAND 사이클)
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

MIN_RISE_PCT = 0.3                 # 종목별 +0.3% 이상이면 "상승" 카운트
MIN_RISING_COUNT = 2               # 3종목(SOX/NVDA/MU) 중 N개 이상 상승 시 매수
MAX_FETCH_RETRIES = 3              # yfinance 실패 시 재시도 횟수
FETCH_RETRY_GAP_SEC = 60           # 재시도 간격 (1분)
LOOP_SLEEP_SEC = 30                # 스케줄러 폴링 간격


# ─────────────────────────────────────────────────────────
# Pure helpers (단위 테스트용)
# ─────────────────────────────────────────────────────────
def evaluate_pre_market(sox_pct, nvda_pct, mu_pct,
                        threshold: float = MIN_RISE_PCT,
                        min_count: int = MIN_RISING_COUNT) -> dict:
	"""SOX/NVDA/MU 등락률 다수결 평가 (3종목 중 min_count 이상 상승 → pass).

	None인 종목은 "상승 아님"으로 카운트 (보수적).
	모두 None이면 fetch_ok=False.

	Returns: {
	  'fetch_ok': bool,    # 최소 1개라도 fetch 성공
	  'sox_ok' / 'nvda_ok' / 'mu_ok': bool,
	  'rising_count': int,
	  'pass': bool,
	}
	"""
	if sox_pct is None and nvda_pct is None and mu_pct is None:
		return {'fetch_ok': False, 'sox_ok': False, 'nvda_ok': False, 'mu_ok': False,
		        'rising_count': 0, 'pass': False}
	sox_ok = sox_pct is not None and sox_pct >= threshold
	nvda_ok = nvda_pct is not None and nvda_pct >= threshold
	mu_ok = mu_pct is not None and mu_pct >= threshold
	rising_count = int(sox_ok) + int(nvda_ok) + int(mu_ok)
	return {
		'fetch_ok': True,
		'sox_ok': sox_ok,
		'nvda_ok': nvda_ok,
		'mu_ok': mu_ok,
		'rising_count': rising_count,
		'pass': rising_count >= min_count,
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


def load_semi_result(eval_date_iso: str = None) -> Optional[dict]:
	"""daily_semi_trigger.json 로드 (어제 16:00 산출 결과).

	Args:
		eval_date_iso: 검증할 date (YYYY-MM-DD). None이면 검증 없이 그대로 반환.

	Returns: output dict 또는 None.
	"""
	import json as _json
	import os as _os
	base = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
	path = _os.path.join(base, 'config', 'data', 'daily_semi_trigger.json')
	if not _os.path.exists(path):
		return None
	try:
		with open(path, 'r', encoding='utf-8') as f:
			data = _json.load(f)
		if eval_date_iso and data.get('date') != eval_date_iso:
			# 일치 안 하는 stale 데이터
			return None
		return data
	except Exception:
		return None


def get_semi_target_for(stock_code: str) -> Optional[str]:
	"""stick 종목 → semi 평가 대상 기초종목 코드.

	Returns:
	  - 005930/000660 직접 → 자기 자신
	  - ETF (ETF_TO_UNDERLYING) → 매핑된 기초종목
	  - 외 → None (semi 적용 안 함, stick fallback)
	"""
	from .semi_trigger.etf_mapping import ETF_TO_UNDERLYING, TARGET_UNDERLYINGS
	if stock_code in TARGET_UNDERLYINGS:
		return stock_code
	return ETF_TO_UNDERLYING.get(stock_code)


def semi_decision_for(stock_code: str, semi_result: Optional[dict]) -> dict:
	"""특정 stick 종목에 대한 semi 결정.

	Returns: {
	  'use_semi': bool,           # semi 결과 적용 가능 여부
	  'target_underlying': str | None,
	  'baseline_sufficient': bool,
	  'trigger': bool,            # semi trigger (use_semi=True일 때만 의미)
	  'semi_score': float | None,
	}

	use_semi=False면 stick fallback 필요.
	"""
	target = get_semi_target_for(stock_code)
	if target is None or semi_result is None:
		return {'use_semi': False, 'target_underlying': target,
		        'baseline_sufficient': False, 'trigger': False, 'semi_score': None}
	# semi targets에서 찾기
	target_info = next(
		(t for t in semi_result.get('targets', []) if t.get('code') == target),
		None,
	)
	if not target_info:
		return {'use_semi': False, 'target_underlying': target,
		        'baseline_sufficient': False, 'trigger': False, 'semi_score': None}
	baseline_ok = bool(target_info.get('baseline_sufficient'))
	return {
		'use_semi':            baseline_ok,
		'target_underlying':   target,
		'baseline_sufficient': baseline_ok,
		'trigger':             bool(target_info.get('trigger')),
		'semi_score':          target_info.get('semi_score'),
	}


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
		"""SOX/NVDA/MU 등락률 조회 → 다수결(3중 2 이상 상승) 시 stick 종목들 buy_queue 추가."""
		from api.external_index import fetch_semi_trio
		from telegram.tel_send import tel_send
		from utils.stick_list import load_stick
		from utils.buy_queue import add_to_queue, load_queue

		self._fetch_attempts += 1
		self._last_fetch_attempt_at = now
		logger.info(f"[stick] pre-market 체크 시도 {self._fetch_attempts}/{MAX_FETCH_RETRIES}")

		result = await fetch_semi_trio()
		sox = result.get('sox')
		nvda = result.get('nvda')
		mu = result.get('mu')

		eval_result = evaluate_pre_market(sox, nvda, mu)

		# 3개 모두 fetch 실패 → 재시도 또는 최종 스킵
		if not eval_result['fetch_ok']:
			if self._fetch_attempts >= MAX_FETCH_RETRIES:
				self._fetched_today = True
				await tel_send(
					f"❌ [stick 데이터 실패] yfinance {MAX_FETCH_RETRIES}회 재시도 실패\n"
					f"SOX={sox} / NVDA={nvda} / MU={mu}\n"
					f"오늘 stick 매수 스킵"
				)
				logger.warning(f"[stick] {MAX_FETCH_RETRIES}회 실패 — 스킵")
			else:
				logger.warning(f"[stick] 전체 fetch 실패 — {FETCH_RETRY_GAP_SEC}초 후 재시도")
			return

		# 평가 결과 표시용 라인
		def _fmt(name, pct, ok):
			if pct is None:
				return f"{name} fetch 실패 ❌"
			return f"{name} {pct:+.2f}% {'✅' if ok else '❌'}"

		status_line = " / ".join([
			_fmt('SOX', sox, eval_result['sox_ok']),
			_fmt('NVDA', nvda, eval_result['nvda_ok']),
			_fmt('MU', mu, eval_result['mu_ok']),
		])

		self._fetched_today = True

		if not eval_result['pass']:
			await tel_send(
				f"⏸️ [stick 조건 미달] 상승 {eval_result['rising_count']}/3 "
				f"(기준 {MIN_RISING_COUNT}/3 이상, 각 +{MIN_RISE_PCT}%↑)\n"
				f"  {status_line}\n"
				f"→ 오늘 stick 매수 스킵"
			)
			logger.info(f"[stick] 조건 미달 ({eval_result['rising_count']}/3): {status_line}")
			return

		# 조건 충족 — stick 종목들 buy_queue에 추가
		items = await load_stick()
		if not items:
			await tel_send(
				f"✅ [stick 조건 충족] 상승 {eval_result['rising_count']}/3\n"
				f"  {status_line}\n"
				f"⚠️ 등록된 stick 종목 없음"
			)
			logger.info("[stick] stick_list 비어있음")
			return

		# semi 결과 로드 (어제 16:00 산출 — 오늘 매수 결정 우선)
		from datetime import date as _date
		today_iso = _date.today().isoformat()
		# semi JSON은 어제 종가 기준이라 date != today일 수 있음 — 검증 없이 로드
		semi_result = load_semi_result(eval_date_iso=None)

		added, duplicate, semi_skipped = [], [], []
		semi_used_codes = []
		for it in items:
			code = it.get('code')
			qty = int(it.get('qty', 1) or 1)
			tpr = it.get('tpr')
			slr = it.get('slr')

			# semi 우선 판단
			decision = semi_decision_for(code, semi_result)
			if decision['use_semi']:
				semi_used_codes.append(code)
				if not decision['trigger']:
					semi_skipped.append((code, decision['semi_score']))
					continue  # semi 매수 안 함

			if await add_to_queue(code, approved_by='stick', qty=qty,
			                       source='stick', tpr=tpr, slr=slr):
				added.append((code, qty))
			else:
				duplicate.append(code)

		queue = await load_queue()
		lines = [
			f"✅ [stick 조건 충족 — stick 룰] 상승 {eval_result['rising_count']}/3",
			f"  {status_line}",
			f"매수 대기열 진입: 추가 {len(added)} / 중복 {len(duplicate)} / semi 스킵 {len(semi_skipped)}",
		]
		if semi_used_codes:
			lines.append(f"📊 semi 우선 적용: {', '.join(semi_used_codes)}")
		if semi_skipped:
			lines.append("⏸️ semi trigger 미달: " + ", ".join(
				f"{c}({s:+.3f})" if s is not None else c for c, s in semi_skipped
			))
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
