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

# 장 시작 동시호가 (Lee 6/2 — auction 명령 처리 윈도우)
OPEN_AUCTION_START = time(8, 30)
OPEN_AUCTION_END = time(8, 50)

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
		if h.get('source') in ('stick', 'auction', 'touch')
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
		if h.get('source') in ('stick', 'auction', 'touch')
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
		self._auction_attempted_today: bool = False  # 08:30 auction 매수 1회

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
		self._auction_attempted_today = False

	async def _scheduler_loop(self):
		while True:
			try:
				now = datetime.now()
				cur_t = now.time()

				# 자정 0시대 — 일일 플래그 리셋
				if now.hour == 0:
					self._reset_daily_flags()

				# 08:30~08:45 pre-market 자동 매수 — Lee 6/2 결정으로 차단됨.
				# semi 정보는 SnapshotScheduler(02:00/05:30)와 텔레그램 score 명령으로 조회.

				# 08:30~08:50 동시호가 매수 (auction 명령) — Lee 6/2: 필터 없이 시장가
				if OPEN_AUCTION_START <= cur_t < OPEN_AUCTION_END and not self._auction_attempted_today:
					self._auction_attempted_today = True
					await self._do_auction_buy()

				# 15:20 1차 동시호가 매도 — stick 등록 종목 청산 (유지)
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
		"""08:30 — semi_trigger morning pipeline (us_mem+fx+NQ 재계산) → 통합 score → 매수 결정.

		Lee 6/2 수정: 기존 stick binary 룰 (SOX+NVDA+MU 2/3) 폐기. semi_score만 사용.
		"""
		from telegram.tel_send import tel_send
		from utils.stick_list import load_stick
		from utils.buy_queue import add_to_queue, load_queue
		from .semi_trigger.pipeline import run_pipeline_morning
		from .semi_trigger import db as st_db
		from .semi_trigger.etf_mapping import TARGET_UNDERLYINGS

		self._fetch_attempts += 1
		self._last_fetch_attempt_at = now
		logger.info(f"[stick] morning 시도 {self._fetch_attempts}/{MAX_FETCH_RETRIES}")

		# 토큰
		token = None
		try:
			token = await self.bot.token_manager.get_token()
		except Exception:
			logger.exception("[stick] 토큰 획득 실패")
		if not token:
			if self._fetch_attempts >= MAX_FETCH_RETRIES:
				self._fetched_today = True
				await tel_send("❌ [stick] 토큰 없음 — 매수 스킵")
			return

		# eval_date — daily_factors 가장 최근 row (어제 evening 저장)
		recent = st_db.fetch_recent_factors(TARGET_UNDERLYINGS[0], n=1)
		if not recent:
			self._fetched_today = True
			await tel_send(
				"⚠️ [stick] daily_factors 비어있음 — 어제 evening pipeline 실패 추정\n"
				"→ 오늘 매수 스킵"
			)
			return
		eval_date = recent[0]['date']

		# morning pipeline (us_mem + fx + nq 재계산 + 통합 score)
		try:
			output = await run_pipeline_morning(eval_date, token, mode='live')
		except Exception:
			logger.exception("[stick] morning pipeline 실패")
			if self._fetch_attempts >= MAX_FETCH_RETRIES:
				self._fetched_today = True
				await tel_send(
					f"❌ [stick] morning pipeline {MAX_FETCH_RETRIES}회 실패 — 매수 스킵"
				)
			return

		self._fetched_today = True

		# 각 stick 종목별 매수 결정
		items = await load_stick()
		targets = output.get('targets', [])
		semi_by_code = {t['code']: t for t in targets}

		# 상태 메시지 (각 종목 raw + score)
		status_lines = []
		for t in targets:
			fr = t.get('factors_raw', {})
			fz = t.get('factors_z', {})
			score = t.get('semi_score')
			base = t.get('baseline_days', 0)
			base_ok = t.get('baseline_sufficient')
			trig = t.get('trigger')
			um = fr.get('us_memory')
			fx = fr.get('fx_change')
			nq = fr.get('nasdaq_futures')
			score_str = f"{score:+.3f}" if score is not None else "N/A"
			marker = "🎯" if trig else ("⏸️" if base_ok else "⏳baseline")
			status_lines.append(
				f"  {t['code']} {t['name']}: semi {score_str} {marker}  "
				f"(us_mem {um:+.2f}% / fx {fx:+.2f}% / NQ {nq:+.2f}% / baseline {base}일)"
				if (um is not None and fx is not None and nq is not None)
				else f"  {t['code']} {t['name']}: semi {score_str} {marker}  (raw 부족)"
			)

		if not items:
			lines = [f"📊 [semi_trigger {eval_date} morning]"]
			lines.extend(status_lines)
			lines.append("⚠️ stick 등록 종목 없음 — 매수 안 함")
			await tel_send("\n".join(lines))
			return

		added, skipped, unsupported = [], [], []
		for it in items:
			code = it.get('code')
			qty = int(it.get('qty', 1) or 1)
			tpr = it.get('tpr')
			slr = it.get('slr')

			target = get_semi_target_for(code)
			if not target or target not in semi_by_code:
				unsupported.append(code)
				continue

			ti = semi_by_code[target]
			if not ti.get('baseline_sufficient'):
				skipped.append((code, f"baseline 부족({ti.get('baseline_days', 0)}일)"))
				continue
			if not ti.get('trigger'):
				skipped.append((code, f"semi {ti.get('semi_score'):+.3f} < {DEFAULT_THRESHOLD}"))
				continue

			if await add_to_queue(code, approved_by='stick', qty=qty,
			                       source='stick', tpr=tpr, slr=slr):
				added.append((code, qty, ti.get('semi_score')))
			else:
				skipped.append((code, '대기열 중복'))

		queue = await load_queue()
		lines = [f"📊 [semi_trigger {eval_date} morning]"]
		lines.extend(status_lines)
		lines.append(
			f"\n매수 대기열: 추가 {len(added)} / 스킵 {len(skipped)} / 미지원 {len(unsupported)}"
		)
		if added:
			lines.append("✅ 매수 진입:")
			for c, q, s in added:
				lines.append(f"  • {c} {q}주 (semi {s:+.3f})")
		if skipped:
			lines.append("⏸️ 스킵: " + ", ".join(f"{c}({r})" for c, r in skipped))
		if unsupported:
			lines.append("⚠️ semi 평가 대상 외: " + ", ".join(unsupported))
		lines.append(f"📦 대기열 총 {len(queue)}건 — 09:00 자동 매수 예정")
		await tel_send("\n".join(lines))
		logger.info(f"[stick] 매수 대기열 추가: {added}")

	async def _do_auction_buy(self):
		"""08:30 동시호가 시장가 매수 — auction 명령 등록 종목.

		Lee 6/2 결정: 필터 없이 (자동매매금지·미체결·쿨다운만 안전망).
		갭상승/하락 + held 차단은 우회 — 미국 폭등 시 의도적 매수.
		"""
		from telegram.tel_send import tel_send
		from utils.buy_queue import load_queue, remove_from_queue
		from utils.holdings import add_holding, calc_sell_deadline
		from api.buy_stock import fn_kt10000
		from utils.blocklist_checker import is_blocked
		from utils.sold_stocks_manager import is_in_cooldown
		from utils.get_setting import get_setting
		from datetime import datetime as _dt, date as _date

		queue = await load_queue()
		auctions = [q for q in queue if q.get('source') == 'auction']
		if not auctions:
			logger.info("[auction] 등록 종목 없음")
			return

		token = await self.bot.token_manager.get_token()
		if not token:
			await tel_send("❌ [auction] 토큰 없음 — 매수 스킵")
			return

		today = _date.today().isoformat()
		cooldown_h = get_setting('sell_cooldown_hours', 24)
		results = []
		for entry in auctions:
			code = entry.get('code')
			qty = int(entry.get('qty', 1) or 1)
			tpr = entry.get('tpr')
			slr = entry.get('slr')

			# 최소 안전망 — 자동매매 금지 + 쿨다운만 (Lee 결정)
			if is_blocked(code):
				results.append((code, qty, 'blocked', None))
				continue
			if is_in_cooldown(code, cooldown_h):
				results.append((code, qty, 'cooldown', None))
				continue

			# 시장가 매수 (동시호가 시점) — rc=3 토큰 에러 시 자동 재발급 후 1회 재시도
			rc, ord_no = None, None
			for attempt in range(2):  # 최대 2회 (초회 + 재시도 1)
				try:
					rc, ord_no = await fn_kt10000(
						stk_cd=code, ord_qty=qty, ord_uv=0, token=token,
						order_type='market', skip_timeout=True,
					)
				except Exception as e:
					logger.exception(f"[auction] {code} 매수 예외 attempt={attempt}")
					rc = f'exc:{type(e).__name__}'
					break
				# rc=3 (토큰 무효) → 강제 재발급 후 재시도
				if str(rc) == '3' and attempt == 0:
					logger.warning(f"[auction] {code} rc=3 → 토큰 강제 재발급 후 재시도")
					try:
						token = await self.bot.token_manager.get_token(force_refresh=True)
					except Exception:
						logger.exception("[auction] 토큰 강제 재발급 실패")
						break
					continue
				break

			# 실패 시에도 buy_queue에서 제거 (그날 1회 시도, 09:00에 재처리 방지)
			if rc != 0 and rc != '0':
				await remove_from_queue(code, source='auction')
				results.append((code, qty, f'rc={rc}', None))
				continue

			# holdings 등록 (실 체결가는 09:05 verify로 갱신)
			holding = {
				'code':             code,
				'buy_price':        0,  # 시장가 — 09:05에 갱신
				'buy_qty':          qty,
				'buy_date':         today,
				'buy_datetime':     _dt.now().isoformat(timespec='seconds'),
				'ord_no':           str(ord_no) if ord_no else '',
				'sell_deadline':    calc_sell_deadline(today),
				'status':           'pending_fill',
				'source':           'auction',
			}
			if tpr is not None:
				holding['tpr'] = float(tpr)
			if slr is not None:
				holding['slr'] = float(slr)
			await add_holding(holding)
			await remove_from_queue(code, source='auction')
			results.append((code, qty, 'ordered', ord_no))

		# 0B 등록 (성공 종목들)
		success_codes = [c for c, q, st, on in results if st == 'ordered']
		if success_codes:
			try:
				ws = getattr(self.bot, 'websocket', None)
				if ws is not None and hasattr(ws, '_queue_reg_request'):
					await ws._queue_reg_request(success_codes, ['0B'], force_refresh=False)
			except Exception:
				logger.exception("[auction] 0B 등록 실패")

		# 텔레그램 알림
		lines = [f"🔥 [동시호가 시장가 매수] 시도 {len(results)}건"]
		for c, q, st, on in results:
			if st == 'ordered':
				lines.append(f"  ✅ {c} {q}주 (ord_no {on})")
			elif st == 'blocked':
				lines.append(f"  ❌ {c} 자동매매 금지")
			elif st == 'cooldown':
				lines.append(f"  ❌ {c} 매도 쿨다운 중")
			else:
				lines.append(f"  ❌ {c} {st}")
		lines.append("\n09:05 체결 확인 / 09:30 미체결 취소")
		await tel_send("\n".join(lines))
		logger.info(f"[auction] {results}")

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
