"""
1차/2차 풀 모니터링 + 매수 결정.

흐름:
    조건검색 매칭 + D-score >= D_SCORE_MIN → pool_monitor.add_to_pool()
      ↓
    1차 풀 5분 모니터링
      - 가격 -2% 지속 15초 → 즉시 2차 풀
      - 5분 만료 시 3항목 (가격/체결강도/거래량) 평가
          모두 OK → 매수
          1개 이상 위반 → 2차 풀
      ↓
    2차 풀 10분 후 마지막 5분 데이터로 재평가
      모두 OK → 매수
      위반 → 폐기
      ↓
    매수 한도: 하루 5개
    1~3번째: D-score 임계만 통과하면 매수
    4~5번째: 기존 매수 중 최저 d_score 초과 시만 매수

임계값은 본 모듈 상단 상수로 관리 (yaml 미사용).
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sector.pool_buffer import get_buffer

logger = logging.getLogger(__name__)

# ============ 임계 상수 ============
# 풀 진입 D-score 임계
D_SCORE_MIN = 6  # 2026-05-19: 7→6 하향 (N=44 표본에서 7+ 통과율 4.5% → 6+ 13.6%)

# 1차 풀
PRIMARY_WAIT_SEC = 300                # 5분
PRICE_BREAK_THRESHOLD = 0.98          # -2%
PRICE_BREAK_PERSIST_SEC = 15          # -2% 지속 시간
STRENGTH_MIN = 110                    # 체결강도 5분 평균 최소
VOLUME_DROP_RATIO = 0.5               # 마지막 1분 < baseline × 0.5 → 위반
VOLUME_RECENT_WINDOW_SEC = 60         # recent = 마지막 1분

# 2차 풀
SECONDARY_WAIT_SEC = 600              # 10분
SECONDARY_RECHECK_WINDOW_SEC = 300    # 재평가 윈도우 = 마지막 5분

# 0B 등록 한도
MAX_SUBSCRIPTIONS = 95
WARNING_THRESHOLD = 0.80
CRITICAL_THRESHOLD = 0.95

# 매수 한도
DAILY_BUY_LIMIT = 5                   # 하루 최대 5종목
TOP_N_INITIAL = 3                     # 초기 매수 상위 N개
BUY_QUANTITY = 1                      # 1주씩


class PoolMonitor:
	"""1차/2차 풀 + 매수 결정."""

	def __init__(self, websocket):
		"""websocket: WebSocket 인스턴스 (자체 _queue_reg_request, _unregister_stock_quote 등 사용)."""
		self.ws = websocket
		self.buffer = get_buffer()

		# 1차 풀: code -> task_data
		self._primary: dict = {}
		# 2차 풀: code -> task_data
		self._secondary: dict = {}

		# 매수 기록 (오늘 매수된 종목)
		self._bought_today: list = []
		# [{'code': str, 'd_score': int, 'bought_at': datetime}]

		# 매수 한도 도달 여부
		self._daily_limit_reached = False

		# 일별 리셋 task (백그라운드)
		self._daily_reset_task: Optional[asyncio.Task] = None

	# ============================================================
	# 풀 진입
	# ============================================================
	async def add_to_pool(self, code: str, signal_price: float, signal_volume: int, d_score: int):
		"""조건검색 통과 + D-score 임계 통과 종목 1차 풀 진입."""
		if self._daily_limit_reached:
			logger.info(f"daily limit reached, skip pool entry: {code}")
			return

		if d_score < D_SCORE_MIN:
			# 임계 미달은 조용히 skip (로그 시끄러움 방지)
			return

		if code in self._primary or code in self._secondary:
			return

		# 보유 종목이면 중복 매수 회피
		try:
			portfolio = getattr(self.ws, 'portfolio', None) or {}
			stock_data = portfolio.get(code, {}) or {}
			if int(stock_data.get('quantity', 0) or 0) > 0:
				logger.info(f"already holding, skip pool entry: {code}")
				return
		except Exception:
			pass

		# 0B 등록
		try:
			await self._register_quote(code)
		except RuntimeError as e:
			logger.warning(f"subscription failed for {code}: {e}")
			return

		task_data = {
			'pool_stage': 'primary',
			'signal_time': datetime.now(),
			'signal_price': float(signal_price),
			'signal_volume': int(signal_volume),
			'volume_baseline': int(signal_volume),
			'd_score': int(d_score),
			'price_break_first_at': None,
			'task': None,
		}
		self._primary[code] = task_data

		task = asyncio.create_task(self._monitor_primary(code))
		task_data['task'] = task

		try:
			from utils.collection_pool import get_stock_name
			name = await get_stock_name(code)
		except Exception:
			name = ''
		name_part = f" {name}" if name else ""
		logger.info(f"primary entered: {code}{name_part} d_score={d_score} price={signal_price}")

	# ============================================================
	# 0B push 처리 (websocket.py에서 dispatch)
	# ============================================================
	def on_quote(self, code: str, price: float, strength: float, volume: int):
		"""0B push 수신 → buffer 누적 + 1차 풀 가격 즉시 체크."""
		self.buffer.append(code, price, strength, volume)
		# buffer flush는 비동기 — 별도 호출 (on_quote는 sync 컨텍스트라 await 못 함)

		if code in self._primary:
			self._check_price_immediate(code, price)

	def _check_price_immediate(self, code: str, current_price: float):
		"""1차 풀 가격 -2% 지속 15초 체크."""
		data = self._primary.get(code)
		if data is None:
			return
		signal_price = data['signal_price']

		if current_price <= signal_price * PRICE_BREAK_THRESHOLD:
			if data['price_break_first_at'] is None:
				data['price_break_first_at'] = datetime.now()
				logger.info(f"price break candidate: {code} {current_price}/{signal_price}")
			elif (datetime.now() - data['price_break_first_at']).total_seconds() >= PRICE_BREAK_PERSIST_SEC:
				logger.info(f"price break confirmed: {code}")
				asyncio.create_task(self._move_to_secondary(code, reason='price_drop_2pct'))
		else:
			if data['price_break_first_at'] is not None:
				data['price_break_first_at'] = None

	# ============================================================
	# 1차 풀 모니터링
	# ============================================================
	async def _monitor_primary(self, code: str):
		try:
			await asyncio.sleep(PRIMARY_WAIT_SEC)

			if code not in self._primary:
				return  # 이미 2차로 이동됨

			data = self._primary[code]
			violations = self._evaluate_5min(code, data)

			if not violations:
				logger.info(f"primary passed: {code}")
				await self._execute_buy(code, data)
			else:
				logger.info(f"primary violated ({violations[0]}): {code}")
				await self._move_to_secondary(code, reason=violations[0])

		except asyncio.CancelledError:
			logger.info(f"primary cancelled: {code}")
			raise
		except Exception:
			logger.exception(f"primary error: {code}")
		finally:
			await self._cleanup_primary(code)

	def _evaluate_5min(self, code: str, data: dict) -> list:
		"""3항목 평가. 위반 항목 리스트 반환."""
		history = self.buffer.get_history(code)
		if history is None:
			return ['no_data']

		violations = []

		# 1) 가격
		if history['price']:
			current_price = history['price'][-1][1]
			if current_price < data['signal_price'] * PRICE_BREAK_THRESHOLD:
				violations.append('price_drop_2pct')

		# 2) 체결강도 5분 평균
		cutoff = datetime.now() - timedelta(seconds=PRIMARY_WAIT_SEC)
		strengths = [v for t, v in history['strength'] if t >= cutoff]
		if not strengths:
			violations.append('no_strength_data')
		else:
			avg = sum(strengths) / len(strengths)
			if avg < STRENGTH_MIN:
				violations.append('weak_strength')

		# 3) 거래량 마지막 1분 vs baseline
		recent_cutoff = datetime.now() - timedelta(seconds=VOLUME_RECENT_WINDOW_SEC)
		recent_volumes = [v for t, v in history['volume'] if t >= recent_cutoff]
		recent_sum = sum(recent_volumes) if recent_volumes else 0
		if recent_sum < data['volume_baseline'] * VOLUME_DROP_RATIO:
			violations.append('volume_drop')

		return violations

	# ============================================================
	# 2차 풀
	# ============================================================
	async def _move_to_secondary(self, code: str, reason: str):
		"""1차 → 2차 이동."""
		data = self._primary.pop(code, None)
		if data is None:
			return

		# 기존 1차 task cancel (cleanup은 task 자체 finally에서)
		old_task = data.get('task')
		if old_task and not old_task.done():
			old_task.cancel()
			# cleanup은 cancel 후 task의 finally가 처리

		sec_data = {
			**data,
			'pool_stage': 'secondary',
			'moved_at': datetime.now(),
			'move_reason': reason,
			'task': None,
			'price_break_first_at': None,
		}
		self._secondary[code] = sec_data

		# 0B 등록 유지 (1차에서 cleanup이 unregister하지 않게 plain dict 제거만)
		# 단 cleanup_primary가 unregister 호출하므로 2차에서 재등록 필요
		try:
			await self._register_quote(code)
		except RuntimeError as e:
			logger.warning(f"secondary re-register failed: {code}: {e}")
			self._secondary.pop(code, None)
			return

		task = asyncio.create_task(self._monitor_secondary(code))
		sec_data['task'] = task

		logger.info(f"secondary entered: {code} reason={reason}")

	async def _monitor_secondary(self, code: str):
		try:
			await asyncio.sleep(SECONDARY_WAIT_SEC)

			if code not in self._secondary:
				return

			data = self._secondary[code]
			violations = self._evaluate_secondary(code, data)

			if not violations:
				logger.info(f"secondary passed: {code}")
				await self._execute_buy(code, data)
			else:
				logger.info(f"secondary discarded: {code} violations={violations}")

		except asyncio.CancelledError:
			logger.info(f"secondary cancelled: {code}")
			raise
		except Exception:
			logger.exception(f"secondary error: {code}")
		finally:
			await self._cleanup_secondary(code)

	def _evaluate_secondary(self, code: str, data: dict) -> list:
		"""마지막 5분 데이터로 재평가."""
		history = self.buffer.get_history(code)
		if history is None:
			return ['no_data']

		violations = []

		if history['price']:
			current_price = history['price'][-1][1]
			if current_price < data['signal_price'] * PRICE_BREAK_THRESHOLD:
				violations.append('price_not_recovered')

		cutoff = datetime.now() - timedelta(seconds=SECONDARY_RECHECK_WINDOW_SEC)
		strengths = [v for t, v in history['strength'] if t >= cutoff]
		if not strengths:
			violations.append('no_strength_data')
		else:
			avg = sum(strengths) / len(strengths)
			if avg < STRENGTH_MIN:
				violations.append('weak_strength')

		recent_cutoff = datetime.now() - timedelta(seconds=VOLUME_RECENT_WINDOW_SEC)
		recent_volumes = [v for t, v in history['volume'] if t >= recent_cutoff]
		recent_sum = sum(recent_volumes) if recent_volumes else 0
		if recent_sum < data['volume_baseline'] * VOLUME_DROP_RATIO:
			violations.append('volume_drop')

		return violations

	# ============================================================
	# 매수
	# ============================================================
	async def _execute_buy(self, code: str, data: dict):
		"""매수 조건 + 실행."""
		if self._daily_limit_reached:
			return

		bought_count = len(self._bought_today)
		if bought_count >= DAILY_BUY_LIMIT:
			self._daily_limit_reached = True
			logger.info(f"daily limit reached ({DAILY_BUY_LIMIT})")
			return

		d_score = data['d_score']

		# 4번째 이후 — 기존 매수 중 최저 점수 초과 필수
		if bought_count >= TOP_N_INITIAL:
			min_score = min(b['d_score'] for b in self._bought_today)
			if d_score <= min_score:
				logger.info(f"d_score {d_score} not exceeding min {min_score}, skip: {code}")
				return

		try:
			success = await self._place_order(code, BUY_QUANTITY)
			if success:
				self._bought_today.append({
					'code': code,
					'd_score': d_score,
					'bought_at': datetime.now(),
				})
				logger.info(f"BUY: {code} d_score={d_score} qty={BUY_QUANTITY} "
				            f"({len(self._bought_today)}/{DAILY_BUY_LIMIT})")
				if len(self._bought_today) >= DAILY_BUY_LIMIT:
					self._daily_limit_reached = True
					logger.info(f"daily limit reached after {code}")
			else:
				logger.warning(f"BUY FAILED: {code}")
		except Exception:
			logger.exception(f"buy error: {code}")

	async def _place_order(self, code: str, qty: int) -> bool:
		"""호가 조회 → 매수 (check_n_buy.py 패턴)."""
		try:
			from api.check_bid import fn_ka10004 as check_bid
			from api.buy_stock import fn_kt10000 as buy_stock
		except ImportError:
			logger.exception("매수 API import 실패")
			return False

		token = getattr(self.ws, 'token', None)
		if not token:
			logger.warning(f"token 없음 — 매수 불가: {code}")
			return False

		try:
			bid = int(await check_bid(code, token=token))
		except Exception:
			logger.exception(f"호가 조회 실패: {code}")
			return False
		if bid <= 0:
			logger.warning(f"호가 0 이하: {code}")
			return False

		try:
			ret_code, _ = await buy_stock(code, qty, bid, token=token)
			return ret_code == 0
		except Exception:
			logger.exception(f"buy_stock 실행 실패: {code}")
			return False

	# ============================================================
	# 0B 등록/해제
	# ============================================================
	async def _register_quote(self, code: str):
		"""0B 등록 + 한도 체크. 한도 95% 도달 시 등록 차단."""
		current = self._count_active_subscriptions()
		ratio = current / MAX_SUBSCRIPTIONS

		if ratio >= CRITICAL_THRESHOLD:
			logger.warning(f"0B subscription critical: {current}/{MAX_SUBSCRIPTIONS}")
			raise RuntimeError(f"subscription limit reached: {current}/{MAX_SUBSCRIPTIONS}")
		elif ratio >= WARNING_THRESHOLD:
			logger.warning(f"0B subscription {ratio*100:.0f}%: {current}/{MAX_SUBSCRIPTIONS}")

		try:
			await self.ws._queue_reg_request([code], ['0B'], force_refresh=False)
		except Exception:
			logger.exception(f"_queue_reg_request 실패: {code}")
			raise

	async def _unregister_quote(self, code: str):
		"""0B 해제 — 기존 _unregister_stock_quote 함수 사용."""
		try:
			await self.ws._unregister_stock_quote(code)
		except Exception:
			logger.exception(f"unregister 실패: {code}")

	def _count_active_subscriptions(self) -> int:
		"""전체 0B 등록 수 (registered_items 기준)."""
		try:
			return len(getattr(self.ws, 'registered_items', {}) or {})
		except Exception:
			return len(self._primary) + len(self._secondary)

	# ============================================================
	# cleanup
	# ============================================================
	async def _cleanup_primary(self, code: str):
		self._primary.pop(code, None)
		# 2차 풀로 이동된 경우 buffer/0B는 유지 (2차에서 계속 사용)
		if code not in self._secondary:
			await self._unregister_quote(code)
			self.buffer.remove(code)

	async def _cleanup_secondary(self, code: str):
		self._secondary.pop(code, None)
		await self._unregister_quote(code)
		self.buffer.remove(code)

	# ============================================================
	# 일별 리셋
	# ============================================================
	def reset_daily_state(self):
		"""일별 매수 카운터 리셋."""
		self._bought_today.clear()
		self._daily_limit_reached = False
		logger.info("daily buy state reset")

	def start_daily_reset_loop(self):
		"""매일 09:00 자동 reset task 시작 (싱글톤)."""
		if self._daily_reset_task and not self._daily_reset_task.done():
			return
		self._daily_reset_task = asyncio.create_task(self._daily_reset_loop())

	async def _daily_reset_loop(self):
		"""매일 09:00 reset."""
		while True:
			try:
				now = datetime.now()
				next_900 = now.replace(hour=9, minute=0, second=0, microsecond=0)
				if next_900 <= now:
					next_900 += timedelta(days=1)
				sleep_sec = (next_900 - now).total_seconds()
				await asyncio.sleep(sleep_sec)
				self.reset_daily_state()
			except asyncio.CancelledError:
				raise
			except Exception:
				logger.exception("daily reset loop error")
				await asyncio.sleep(60)  # 에러 시 1분 후 재시도


# ============================================================
# 모듈 싱글톤
# ============================================================
_global_monitor: Optional[PoolMonitor] = None


def set_monitor(monitor: PoolMonitor):
	global _global_monitor
	_global_monitor = monitor


def get_monitor() -> Optional[PoolMonitor]:
	return _global_monitor


async def evaluate_and_add(code: str):
	"""
	조건검색 매칭 시 호출. D-score 산출 → 임계(D_SCORE_MIN 이상) 통과 시 1차 풀 진입.
	signal_price/signal_volume은 일봉 마지막 행의 close/volume 사용.

	rt_search.py / websocket.py의 조건검색 매칭 지점에서 호출.
	"""
	monitor = get_monitor()
	if monitor is None:
		return
	try:
		from tools.data_loaders import load_7d_bars
		from sector.candle_quality import evaluate_candle_quality

		bars = load_7d_bars(code)
		if bars is None or len(bars) < 7:
			return
		r = evaluate_candle_quality(bars)
		d_score = int(r['score'])
		if d_score < D_SCORE_MIN:
			return
		signal_price = float(bars.iloc[-1]['close'])
		signal_volume = int(bars.iloc[-1]['volume'])
		await monitor.add_to_pool(code, signal_price, signal_volume, d_score)
	except Exception:
		logger.exception(f"evaluate_and_add error: {code}")
