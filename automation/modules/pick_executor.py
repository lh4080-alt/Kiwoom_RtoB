"""
pick_executor — pick 명령 장중 신호기반 진입 (하락 case). touch_executor와 병렬 모듈 (touch 무손상).

설계 (Lee 6/15):
- 기준 = 시가(O). `09:00 + entry_delay`(분)부터 15:20까지 0B push로 감시. 첫 충족 시 1회 시장가 매수.
- drop = (현재가−O)/O×100 (하락이면 음수). 비교는 직전 1분 윈도우 기준.
- 신호 (각 ↑):
    a 가격상승   : 현재가 > 직전 1분 평균가
    b 체결강도↑  : 현재 체결강도 > 직전 1분 평균 체결강도
    c 거래량↑    : 최근 1분 거래량(누적증가분) > 직전 1분 거래량
    d 매수우위(체결): 0B 매수비율(1032) > 50% (없으면 1031>1030) — 추가 API 없음
- 밴드 (down_min = 설정 하한, -1% 분기선 고정):
    0% > drop ≥ -1%        : a∧b∧c        → 매수 (얕은 하락)
    -1% > drop ≥ down_min  : a∧b∧c∧d      → 매수 (깊은 하락, 더 엄격)
    drop < down_min        : 매수 금지 (범위 복귀까지 대기)
- 시장가 매수 1회 (touch와 동일 패턴). holdings source='pick'.

settings: pick_down_min(기본 -2.0), pick_entry_delay(분, 기본 10)

0B FID (명세 p.477 확정): 현재가=10, 거래량=15, 누적거래량=13, 체결강도=228, 매수비율=1032.
   d(매수비율 1032)는 명세 예시에서 빈 값일 수 있음 → [pick raw 0B] 로그로 실제 채워지는지 확인.
   비어도 deep band만 보류(얕은 band는 정상). 상승 case는 a 대신 '신고가 갱신' 사용.
"""
import asyncio
import logging
from datetime import datetime, date, time
from typing import Optional

logger = logging.getLogger(__name__)

MARKET_OPEN = time(9, 0)
CLOSING_AUCTION = time(15, 20)
FALLBACK_POLL_INTERVAL = 60  # 1분 — 0B 누락 대비 (신호 평가는 push 기반이 주)

# 0B 주식체결 실시간 FID (현재가/거래량은 봇 코드 확인, 체결강도/누적거래량은 키움 표준 추정)
FID_PRICE = '10'
FID_STRENGTH = '228'
FID_VOL_CUM = '13'

DEEP_LINE_PCT = -1.0   # 하락 -1% 분기선 (고정): 0~-1%=3조건, -1%~down_min=4조건
UP_DEEP_LINE_PCT = 1.0 # 상승 +1% 분기선 (고정): 0~+1%=3조건, +1%~up_min=4조건
LOOKBACK_SEC = 60      # 직전 1분 윈도우


def _to_float(raw) -> float:
	"""키움 부호 문자열 → float. '--'/'-'/'+' 처리, 실패 시 0."""
	if raw is None:
		return 0.0
	if isinstance(raw, (int, float)):
		return float(raw)
	s = str(raw).strip().replace(',', '')
	if not s:
		return 0.0
	neg = False
	while s and s[0] in '+-':
		if s[0] == '-':
			neg = not neg
		s = s[1:]
	try:
		f = float(s)
	except ValueError:
		return 0.0
	return -f if neg else f


class PickExecutor:
	def __init__(self, bot_ref):
		self.bot = bot_ref
		self._task: Optional[asyncio.Task] = None
		self._check_lock = asyncio.Lock()
		self._selling_codes: set = set()  # (사용 안 함 — 진입 전용; 향후 확장 여지)
		# 시가 캐시 {code: open_price}
		self._open: dict = {}
		# 1분 윈도우용 히스토리 {code: [(t_epoch, price, strength, cum_vol), ...]}
		self._hist: dict = {}
		# 진입 시도 중 직렬화 {code} — 중복 매수 방지
		self._buying: set = set()
		# FID 검증 1회 로그용 (체결강도 228 / 누적거래량 13 실제 존재 확인)
		self._logged_fids: set = set()
		# 세션 최고가 {code: high} — 상승 case 신고가 갱신 판정
		self._session_high: dict = {}
		# 매수우위(체결 기반) {code: bool} — d 신호 (0B 매수비율 1032 > 50)
		self._buy_dom: dict = {}

	def start(self):
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._scheduler_loop())
			asyncio.create_task(self._reregister_queue_on_startup())
			logger.info("PickExecutor started (0B push 신호기반 + 1분 fallback)")

	def stop(self):
		if self._task and not self._task.done():
			self._task.cancel()

	# ── 설정 ────────────────────────────────────────────────
	def _down_min(self) -> float:
		from utils.get_setting import get_setting
		return float(get_setting('pick_down_min', -2.0))

	def _up_min(self) -> float:
		from utils.get_setting import get_setting
		return float(get_setting('pick_up_min', 3.0))

	def _entry_delay_min(self) -> int:
		from utils.get_setting import get_setting
		return int(get_setting('pick_entry_delay', 10) or 10)

	def _monitor_active(self) -> bool:
		"""09:00 + entry_delay ~ 15:20 구간인지."""
		now = datetime.now()
		if not (MARKET_OPEN <= now.time() < CLOSING_AUCTION):
			return False
		start = now.replace(hour=9, minute=0, second=0, microsecond=0)
		from datetime import timedelta
		start = start + timedelta(minutes=self._entry_delay_min())
		return now >= start

	# ── 0B 등록 (touch 패턴 재사용) ──────────────────────────
	async def _register_0b(self, codes: list):
		try:
			ws = getattr(self.bot, 'websocket', None)
			if ws is None or not hasattr(ws, '_queue_reg_request'):
				return
			await ws._queue_reg_request(codes, ['0B'], force_refresh=False)
			logger.info(f"[pick] 0B 등록 요청: {codes}")
		except Exception:
			logger.exception(f"[pick] 0B 등록 실패: {codes}")

	async def register_for_pick(self, code: str):
		"""pick 등록 명령에서 호출 — 0B 등록 + 상태 초기화."""
		self._open.pop(code, None)
		self._hist.pop(code, None)
		self._session_high.pop(code, None)
		await self._register_0b([code])

	async def _reregister_queue_on_startup(self):
		"""봇 startup 1회 — 큐의 pick 종목 0B 재등록."""
		from utils.buy_queue import load_queue
		try:
			await asyncio.sleep(3)
			queue = await load_queue()
			codes = [q.get('code') for q in queue if q.get('source') == 'pick']
			if codes:
				await self._register_0b(codes)
				logger.info(f"[pick] startup 0B 재등록: {codes}")
		except Exception:
			logger.exception("[pick] startup 0B 재등록 실패")

	# ── 0B push 핸들러 ───────────────────────────────────────
	async def on_0b_quote(self, code: str, current_price: int, values: dict = None):
		"""0B push마다 호출 — 히스토리 적재 후 트리거 평가."""
		if not code or not current_price:
			return
		# 히스토리 적재 (감시 구간 밖이어도 워밍업 위해 적재)
		t = datetime.now().timestamp()
		v = values or {}
		strength = _to_float(v.get(FID_STRENGTH))
		cum_vol = _to_float(v.get(FID_VOL_CUM))
		# d 신호용 매수우위(체결 기반): 매수비율(1032)>50, 없으면 매수체결량(1031)>매도체결량(1030),
		# 그것도 없으면 순매수체결량(1314)>0. 셋 다 없으면 미갱신(이전 값 유지, 없으면 False→deep band 보류).
		_ratio = _to_float(v.get('1032'))
		_bvol = _to_float(v.get('1031'))
		_svol = _to_float(v.get('1030'))
		_net = _to_float(v.get('1314'))
		if _ratio > 0:
			self._buy_dom[code] = _ratio > 50.0
		elif _bvol > 0 or _svol > 0:
			self._buy_dom[code] = _bvol > _svol
		elif _net != 0:
			self._buy_dom[code] = _net > 0
		# FID 검증용 1회 로그 — 체결강도(228)·누적거래량(13)·매수비율(1032) 실제 0B 존재 확인
		if values and code not in self._logged_fids:
			self._logged_fids.add(code)
			logger.info(f"[pick raw 0B {code}] 228(체결강도)={v.get('228')} 13(누적거래량)={v.get('13')} "
			            f"15(거래량)={v.get('15')} 1032(매수비율)={v.get('1032')} 1031={v.get('1031')} 1030={v.get('1030')} keys={sorted(v.keys())}")
		h = self._hist.setdefault(code, [])
		h.append((t, float(current_price), strength, cum_vol))
		# 2분 초과 항목 prune
		cutoff = t - 2 * LOOKBACK_SEC
		if h and h[0][0] < cutoff:
			self._hist[code] = [r for r in h if r[0] >= cutoff]

		# 세션 신고가 갱신 판정 (상승 case): 직전 최고가 초과 시 True
		prior_high = self._session_high.get(code, 0.0)
		is_new_high = float(current_price) > prior_high
		if is_new_high:
			self._session_high[code] = float(current_price)

		if not self._monitor_active():
			return
		if code in self._buying:
			return
		if self._check_lock.locked():
			return
		async with self._check_lock:
			await self._evaluate_one(code, current_price_hint=current_price, is_new_high=is_new_high)

	# ── fallback (1분) ───────────────────────────────────────
	async def _scheduler_loop(self):
		while True:
			try:
				if self._monitor_active():
					await self._check_all()
			except asyncio.CancelledError:
				return
			except Exception:
				logger.exception("[pick] scheduler 예외")
			await asyncio.sleep(FALLBACK_POLL_INTERVAL)

	async def _check_all(self):
		if self._check_lock.locked():
			return
		async with self._check_lock:
			from utils.buy_queue import load_queue
			queue = await load_queue()
			for entry in [q for q in queue if q.get('source') == 'pick']:
				await self._evaluate_one(entry.get('code'), current_price_hint=None)

	# ── 신호 계산 (직전 1분 윈도우) ──────────────────────────
	def _window_signals(self, code: str, cur: float):
		"""(a, b, c) 반환. 데이터 부족 시 해당 신호 False (오발주 방지)."""
		h = self._hist.get(code, [])
		now = datetime.now().timestamp()
		w1 = [r for r in h if r[0] >= now - LOOKBACK_SEC]              # 직전 1분
		w2 = [r for r in h if now - 2 * LOOKBACK_SEC <= r[0] < now - LOOKBACK_SEC]  # 그 이전 1분

		# a: 현재가 > 직전 1분 평균가
		a = False
		if w1:
			avg_price = sum(r[1] for r in w1) / len(w1)
			a = cur > avg_price

		# b: 현재 체결강도 > 직전 1분 평균 체결강도
		b = False
		strengths = [r[2] for r in w1 if r[2] > 0]
		if strengths and h and h[-1][2] > 0:
			b = h[-1][2] > (sum(strengths) / len(strengths))

		# c: 최근 1분 거래량 증가분 > 직전 1분 (누적거래량 델타 비교)
		c = False
		if w1 and w2:
			vol_last = w1[-1][3] - w1[0][3]
			vol_prev = w2[-1][3] - w2[0][3]
			if w1[-1][3] > 0:  # 누적거래량 유효
				c = vol_last > vol_prev
		return a, b, c

	# ── 트리거 평가 + 매수 ───────────────────────────────────
	async def _evaluate_one(self, code: str, current_price_hint=None, is_new_high: bool = False):
		from telegram.tel_send import tel_send
		from utils.buy_queue import load_queue, remove_from_queue
		from utils.holdings import add_holding, calc_sell_deadline
		from utils.blocklist_checker import is_blocked
		from utils.sold_stocks_manager import is_in_cooldown
		from utils.get_setting import get_setting
		from api.stock_info import fn_ka10001
		from api.buy_stock import fn_kt10000

		if not code or code in self._buying:
			return

		queue = await load_queue()
		entry = next((q for q in queue if q.get('code') == code and q.get('source') == 'pick'), None)
		if not entry:
			return
		qty = int(entry.get('qty', 1) or 1)
		tpr = entry.get('tpr')
		slr = entry.get('slr')

		# 안전망 (보유중 차단은 제외 — Lee 6/15 결정)
		if is_blocked(code):
			return
		if is_in_cooldown(code, get_setting('sell_cooldown_hours', 24)):
			return

		token = await self.bot.token_manager.get_token()
		if not token:
			return

		# 시가 캐시
		open_prc = self._open.get(code, 0.0)
		if open_prc <= 0:
			try:
				info = await fn_ka10001(code, token=token, silent=True)
			except Exception:
				logger.exception(f"[pick] {code} ka10001 실패")
				return
			raw = info.get('raw', {}) if isinstance(info, dict) else {}
			open_prc = _to_float(raw.get('open_pric'))
			if open_prc <= 0:
				return  # 시가 미형성
			self._open[code] = open_prc

		# 현재가
		cur = float(current_price_hint) if current_price_hint else 0.0
		if cur <= 0:
			try:
				info = await fn_ka10001(code, token=token, silent=True)
				cur = float(info.get('cur_prc') or 0)
			except Exception:
				return
		if cur <= 0:
			return

		chg = (cur - open_prc) / open_prc * 100.0   # 시가 대비 등락 %
		down_min = self._down_min()
		up_min = self._up_min()
		a, b, c = self._window_signals(code, cur)

		if chg < 0:
			# ── 하락 case: 반등(a) + 체결강도↑(b) + 거래량↑(c) [+ 매수우위(d)] ──
			if chg < down_min:
				return                   # 바닥 밑 → 매수 금지
			if not (a and b and c):
				return
			need_d = chg < DEEP_LINE_PCT  # -1% 밑(깊은 하락)
			direction = '하락'
		elif chg > 0:
			# ── 상승 case: 신고가 갱신 + 체결강도↑(b) + 거래량↑(c) [+ 매수우위(d)] ──
			if chg > up_min:
				return                   # 천장 위 → 추격 금지
			if not is_new_high:
				return                   # 신고가 갱신 시점만 (고점 밑/눌림에선 대기)
			if not (b and c):
				return                   # 체결강도↑·거래량↑ 미충족
			need_d = chg > UP_DEEP_LINE_PCT  # +1% 위(더 오른 구간)
			direction = '상승'
		else:
			return                       # 시가와 동일

		if need_d:
			if not self._buy_dom.get(code, False):
				return  # 깊은 구간 + 매수우위(체결) 미확인 → 보류

		# ── 매수 (touch 패턴) ──
		self._buying.add(code)
		try:
			rc, ord_no = None, None
			for attempt in range(2):
				try:
					rc, ord_no = await fn_kt10000(
						stk_cd=code, ord_qty=qty, ord_uv=0, token=token,
						order_type='market', skip_timeout=True,
					)
				except Exception:
					logger.exception(f"[pick] {code} 매수 예외 attempt={attempt}")
					rc = 'exc'
					break
				if str(rc) == '3' and attempt == 0:
					try:
						token = await self.bot.token_manager.get_token(force_refresh=True)
					except Exception:
						break
					continue
				break

			if rc != 0 and rc != '0':
				await remove_from_queue(code, source='pick')
				await tel_send(f"❌ [pick] {code} 매수 실패 rc={rc} — 큐 제거")
				return

			today = date.today().isoformat()
			holding = {
				'code': code, 'buy_price': 0, 'buy_qty': qty, 'buy_date': today,
				'buy_datetime': datetime.now().isoformat(timespec='seconds'),
				'ord_no': str(ord_no) if ord_no else '',
				'sell_deadline': calc_sell_deadline(today),
				'status': 'pending_fill', 'source': 'pick',
			}
			if tpr is not None:
				holding['tpr'] = float(tpr)
			if slr is not None:
				holding['slr'] = float(slr)
			await add_holding(holding)
			await remove_from_queue(code, source='pick')

			band = '4조건' if need_d else '3조건'
			delay = self._entry_delay_min()
			price_sig = '신고가갱신' if direction == '상승' else '반등(a)'
			logger.info(f"[pick] {code} {qty}주 매수 ord_no={ord_no} {direction} {chg:+.2f}% band={band} delay={delay}m price={price_sig} b/c={b}/{c} d={need_d}")
			await tel_send(
				f"🎯 [pick 매수] {code} {qty}주 (시장가)\n"
				f"  시가={int(open_prc):,} 현재={int(cur):,} ({direction} {chg:+.2f}%)\n"
				f"  {band}·{price_sig} 충족 (delay {delay}분) ord_no {ord_no}"
			)
		finally:
			self._buying.discard(code)
