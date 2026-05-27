"""
16:00 일일 분석 + 텔레그램 알림.

수집풀(collection_pool.json) 모든 종목을 자동 분석한 결과를 텔레그램으로 보고하고,
수집풀과 매수 대기열(buy_queue.json)을 비운다.

종목별 정보:
  - 일봉: 등락률, 거래량비, 종가위치 (\\beelink\\market_data)
  - 체결강도 5분 평균: ka10046
  - 외인/기관 일별 5일치: ka10059 (단위 천주)
  - 프로그램매매 일별 5일치: ka90013 (단위 백만원 → 억원 환산 표시)

영구 원칙 (메모리 #30): collection_pool/buy_queue 조작은 봇 데몬 내부에서만.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 텔레그램 메시지 1건 최대 길이 (Telegram API 한계 4096 — 여유분)
TELEGRAM_MAX_CHARS = 3800

# 텔레그램에 표시할 상위 종목 수 (전체 분석은 하되 보고는 상위 N개)
TOP_N_DISPLAY = 30

# 외인 연속 매수 강조 임계값
FRGNR_STREAK_HIGHLIGHT = 3

# 엣지 분석기 임계값 (5/27 통합 — Lee 지시서 4대 방향)
SMART_MONEY_HIGH_EOK = 20.0      # 투신+연기금 합계 (억원) — 강한 신호
SMART_MONEY_MID_EOK = 5.0        # 투신+연기금 합계 (억원) — 중간 신호
SMART_STREAK_THRESHOLD = 3       # 투신 또는 연기금 3일 이상 연속 매수
CNTR_STR_STRONG = 120.0          # 체결강도 120 이상 = 강한 매수 탄력
PROGRAM_STRONG_BUY_EOK = 5.0     # 프로그램 비차익 양수 임계 (억원)
PROGRAM_HEAVY_SELL_EOK = -10.0   # 프로그램 대량 매도 패널티 임계
PRICE_LOC_STRONG = 80.0          # 종가 위치 80%↑ 진성 양봉
PRICE_LOC_WEAK = 50.0            # 종가 위치 50% 미만 = 윗꼬리 트랩
EDGE_SCORE_PICK_CUT = 70         # 70점 이상 next_day_watchlist 자동 적재


# ─────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────
def _parse_kiwoom_signed(s) -> int:
	"""키움 응답 부호 정수 파싱.

	형식: '--12345' (이중 마이너스, ka90013), '-12345' (단일, ka10059),
	      '+12345', '12345', '' → 0.
	"""
	if s is None:
		return 0
	s = str(s).strip()
	if not s:
		return 0
	if s.startswith('--'):
		s = '-' + s[2:]
	elif s.startswith('+'):
		s = s[1:]
	try:
		return int(s)
	except ValueError:
		try:
			return int(float(s))
		except (ValueError, TypeError):
			return 0


def _parse_kiwoom_float(s) -> float:
	"""키움 응답 부호 실수 파싱 (체결강도 등)."""
	if s is None:
		return 0.0
	s = str(s).strip()
	if not s:
		return 0.0
	if s.startswith('--'):
		s = '-' + s[2:]
	elif s.startswith('+'):
		s = s[1:]
	try:
		return float(s)
	except (ValueError, TypeError):
		return 0.0


def _calc_streak(values: list) -> int:
	"""일별 net 값 ([0]=최신) → 부호 연속 일수.

	+N: 최신부터 N일 연속 매수, -N: 연속 매도, 0: 빈 리스트 또는 최신 0.
	예: [-9283, -4482, -8891, 740, -5705] → -3
	"""
	if not values:
		return 0
	first = values[0]
	if first > 0:
		sign = 1
	elif first < 0:
		sign = -1
	else:
		return 0
	count = 0
	for v in values:
		v_sign = 1 if v > 0 else (-1 if v < 0 else 0)
		if v_sign == sign:
			count += 1
		else:
			break
	return sign * count


def calc_price_location(cur: float, high: float, low: float) -> float:
	"""캔들 내 종가 위치 % — Lee 지시서 ③: 80%↑ 진입, 50%↓ 윗꼬리 트랩.

	(종가 - 저가) / (고가 - 저가) × 100. 분모 0 또는 음수면 50 안전 기본값.
	"""
	if high <= low or cur <= 0:
		return 50.0
	loc = (cur - low) / (high - low) * 100
	return max(0.0, min(loc, 100.0))


def qty_thousand_to_eok(qty_thousand: int, close_price: float) -> float:
	"""ka10059 수량(천주) × 종가 → 억원 정규화. Lee 지시서 ① 핵심 공식.

	예: invtrt=-128(천주), close=295,000원 → -128,000주 × 295,000 / 1억 = -377.6억
	"""
	if close_price <= 0:
		return 0.0
	return (qty_thousand * 1000 * close_price) / 100_000_000


def program_amount_mm_to_eok(net_mm: int) -> float:
	"""ka90013 prm_netprps_amt(백만원) → 억원. 100백만원 = 1억원."""
	return net_mm / 100.0


def calculate_edge_score(
	price_location: float,
	smart_money_eok: float,
	trust_days: int,
	pension_days: int,
	cntr_str_5min: float,
	program_eok: float,
) -> int:
	"""4대 가중치 매트릭스 → 엣지 점수 0~100 (Lee 지시서).

	베이스 50점에서 가/감점. 70점 이상 next_day_watchlist 적재.
	"""
	score = 50

	# 1. 종가 위치 (Bull Trap 회피)
	if price_location >= PRICE_LOC_STRONG:
		score += 15
	elif price_location < PRICE_LOC_WEAK:
		score -= 25

	# 2. 스마트머니 (투신 + 연기금 합산 억원)
	if smart_money_eok >= SMART_MONEY_HIGH_EOK:
		score += 15
	elif smart_money_eok >= SMART_MONEY_MID_EOK:
		score += 10

	# 3. 스마트머니 연속성 (3일 이상)
	if trust_days >= SMART_STREAK_THRESHOLD or pension_days >= SMART_STREAK_THRESHOLD:
		score += 10

	# 4. 체결강도 + 프로그램 매트릭스
	if cntr_str_5min >= CNTR_STR_STRONG and program_eok > PROGRAM_STRONG_BUY_EOK:
		score += 10  # 진성 돌파 + 메이저 패시브 자금 유입
	elif program_eok < PROGRAM_HEAVY_SELL_EOK:
		score -= 15  # 메이저 프로그램 매도 폭탄

	return max(0, min(score, 100))


def _format_program_amount(net_mm: int) -> str:
	"""프로그램 순매수 금액 (백만원) → 표시 (억원 환산).

	100 백만원 = 1 억원. 1억 미만은 '0'.
	예: -2134262 → '-21,343억', +1234 → '+12억', 50 → '0'.
	"""
	if net_mm == 0:
		return '0'
	eok = net_mm / 100.0
	rounded = int(round(eok))
	if rounded == 0:
		return '0'
	sign = '+' if rounded > 0 else '-'
	return f'{sign}{abs(rounded):,}억'


class DailyAnalyzer:
	"""16:00 일일 분석. 수집풀 종목별 정보 + 시장 환경 → 텔레그램 알림."""

	def __init__(self, bot_ref=None):
		"""
		Args:
			bot_ref: ChatCommand 또는 봇 객체.
		"""
		self.bot = bot_ref

	async def run(self):
		"""16:00 트리거 진입점. 분석 + 알림 + 풀 정리."""
		from utils.collection_pool import get_pool, clear_pool
		from utils.buy_queue import clear_queue
		from telegram.tel_send import tel_send

		today = datetime.now().strftime('%Y-%m-%d')
		dt_yyyymmdd = datetime.now().strftime('%Y%m%d')
		logger.info(f"daily_analyzer 시작: {today}")

		try:
			pool = get_pool()
			codes = list(pool.keys()) if isinstance(pool, dict) else []

			if not codes:
				await tel_send(f"[{today} 일일 분석] 오늘 매칭 종목 없음")
				logger.info("수집풀 비어있음 — 분석 스킵")
			else:
				logger.info(f"daily_analyzer 분석 대상: {len(codes)}건")

				# 봇 토큰 매니저 재사용 (캐시 토큰 그대로 — 신규 발급 없음). fallback은 standalone 테스트용.
				token = await self._get_token()
				if not token:
					logger.error("토큰 획득 실패 — 분석 스킵")
					await tel_send(f"❌ [{today} 일일 분석] 토큰 획득 실패")
					return

				results = []
				for code in codes:
					try:
						info = await self._gather_stock_info(code, pool.get(code, {}), token, dt_yyyymmdd)
						results.append(info)
					except Exception:
						logger.exception(f"종목 분석 실패: {code}")
						results.append({'code': code, 'error': 'analysis_failed'})

				market = await self._gather_market_info()
				messages = self._format_telegram(results, market, today)
				for msg in messages:
					await tel_send(msg)

			# 매수 대기열 비우기 (전일 잔재 방지)
			cleared_q = await clear_queue()
			logger.info(f"buy_queue cleared: {cleared_q} entries")

			# 수집풀 비우기 — 영구 원칙 (봇 데몬 내부 처리)
			loop = asyncio.get_event_loop()
			cleared_p = await loop.run_in_executor(None, clear_pool)
			logger.info(f"collection_pool cleared: {cleared_p} entries")

		except Exception:
			logger.exception("daily_analyzer 실패")
			try:
				await tel_send(f"❌ [ERROR] daily_analyzer 실패 ({datetime.now().strftime('%H:%M:%S')})")
			except Exception:
				pass

	async def _get_token(self) -> Optional[str]:
		"""봇 토큰 매니저 우선, 없으면 fn_au10001 fallback (standalone 테스트)."""
		tm = getattr(self.bot, 'token_manager', None) if self.bot else None
		if tm is not None:
			return await tm.get_token()
		from api.login import fn_au10001
		return await fn_au10001()

	# ─────────────────────────────────────────────────────────
	# 종목별 정보 수집
	# ─────────────────────────────────────────────────────────
	async def _gather_stock_info(self, code: str, pool_entry: dict, token: str, dt_yyyymmdd: str) -> dict:
		"""단일 종목 정보 — ka10001(OHLCV) + ka10046(체결강도) + ka10059(수급) + ka90013(프로그램).

		Lee 지시서 4대 엣지 분석:
		  ① 수량 → 억원 정규화 (수량 × 종가 / 1억)
		  ② 투신/연기금 분리 + 3일 연속성
		  ③ 종가 위치 % (80%↑ 진입, 50%↓ Bull Trap)
		  ④ 체결강도 + 프로그램 매트릭스 → edge_score 70+ next_day_watchlist
		"""
		from utils.collection_pool import get_stock_name
		from api.stock_info import fn_ka10001
		from api.stk_strength import fn_ka10046
		from api.inv_trade_trend import fn_ka10059
		from api.program_trade import fn_ka90013

		name = await get_stock_name(code)

		# ka10001 — 종가(cur_prc) + OHLCV (종가 위치 + 억원 정규화 분모용)
		close_price = 0.0
		price_location = 50.0  # 안전 기본
		try:
			tm = getattr(self.bot, 'token_manager', None) if self.bot else None
			if tm is not None and hasattr(tm, 'call_with_auto_refresh'):
				info = await tm.call_with_auto_refresh(fn_ka10001, code, silent=True)
			else:
				info = await fn_ka10001(code, token=token, silent=True)
			if isinstance(info, dict):
				close_price = abs(float(info.get('cur_prc') or 0))
				raw = info.get('raw', {}) or {}
				high = abs(_parse_kiwoom_float(raw.get('high_pric', '')))
				low = abs(_parse_kiwoom_float(raw.get('low_pric', '')))
				if close_price > 0 and high > 0 and low > 0:
					price_location = calc_price_location(close_price, high, low)
		except Exception:
			logger.exception(f"ka10001 호출 실패 {code}")

		# === [폐기] 일봉 의존 (등락률/거래량비/양음봉/종가위치) ===
		# bars = load_7d_bars(code)
		# if bars is None or len(bars) < 2:
		# 	return {
		# 		'code': code,
		# 		'name': name,
		# 		'error': 'no_bars',
		# 		'hit_count': pool_entry.get('hit_count', 0),
		# 		'seq_ids': pool_entry.get('seq_ids', []),
		# 	}
		#
		# today_row = bars.iloc[-1]
		# prev_row = bars.iloc[-2]
		#
		# today_close = float(today_row['close'])
		# prev_close = float(prev_row['close'])
		# chg_pct = ((today_close - prev_close) / prev_close * 100) if prev_close else 0.0
		#
		# today_vol = float(today_row['volume'])
		# prev_vols = bars.iloc[:-1]['volume'].astype(float).tolist()
		# avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0.0
		# vol_ratio = (today_vol / avg_vol * 100) if avg_vol else 0.0
		#
		# today_open = float(today_row['open'])
		# today_high = float(today_row['high'])
		# today_low = float(today_row['low'])
		# is_bullish = today_close > today_open
		# rng = today_high - today_low
		# close_pos = ((today_close - today_low) / rng * 100) if rng else 50.0
		# === [폐기 끝] ===

		# 체결강도 (ka10046) — token_manager wrapper 경유 (rc=3 자동 재발급)
		cntr_str_5min: Optional[float] = None
		try:
			tm = getattr(self.bot, 'token_manager', None) if self.bot else None
			if tm is not None and hasattr(tm, 'call_with_auto_refresh'):
				data = await tm.call_with_auto_refresh(fn_ka10046, code)
			else:
				data = await fn_ka10046(code, token)
			if data.get('return_code') == 0:
				items = data.get('cntr_str_tm', [])
				if items:
					cntr_str_5min = _parse_kiwoom_float(items[0].get('cntr_str_5min', ''))
		except Exception:
			logger.exception(f"ka10046 호출 실패 {code}")

		# 외인/기관 일별 5일치 + 투신/연기금 분리 + 거래량비 (ka10059) — wrapper 경유
		frgnr_today = orgn_today = ind_today = 0
		trust_today = pension_today = 0  # 천주 단위 (raw)
		frgnr_streak = orgn_streak = 0
		trust_streak = pension_streak = 0
		vol_ratio: Optional[float] = None
		try:
			tm = getattr(self.bot, 'token_manager', None) if self.bot else None
			if tm is not None and hasattr(tm, 'call_with_auto_refresh'):
				data = await tm.call_with_auto_refresh(fn_ka10059, code, dt=dt_yyyymmdd)
			else:
				data = await fn_ka10059(code, token, dt_yyyymmdd)
			if data.get('return_code') == 0:
				items = data.get('stk_invsr_orgn', [])[:5]
				if items:
					frgnr_values = [_parse_kiwoom_signed(it.get('frgnr_invsr', '')) for it in items]
					orgn_values = [_parse_kiwoom_signed(it.get('orgn', '')) for it in items]
					ind_values = [_parse_kiwoom_signed(it.get('ind_invsr', '')) for it in items]
					trust_values = [_parse_kiwoom_signed(it.get('invtrt', '')) for it in items]      # 투신
					pension_values = [_parse_kiwoom_signed(it.get('penfnd_etc', '')) for it in items]  # 연기금 등
					frgnr_today = frgnr_values[0]
					orgn_today = orgn_values[0]
					ind_today = ind_values[0]
					trust_today = trust_values[0]
					pension_today = pension_values[0]
					frgnr_streak = _calc_streak(frgnr_values)
					orgn_streak = _calc_streak(orgn_values)
					trust_streak = _calc_streak(trust_values)
					pension_streak = _calc_streak(pension_values)
					# 거래량비: acc_trde_qty (주 단위) today vs prev 4일 평균
					vols = [_parse_kiwoom_signed(it.get('acc_trde_qty', '')) for it in items]
					if len(vols) >= 2:
						prev_vols = [v for v in vols[1:] if v > 0]
						if prev_vols:
							avg_prev = sum(prev_vols) / len(prev_vols)
							if avg_prev > 0:
								vol_ratio = vols[0] / avg_prev * 100
		except Exception:
			logger.exception(f"ka10059 호출 실패 {code}")

		# 프로그램매매 일별 5일치 (ka90013) — wrapper 경유
		prm_net_mm_today = 0
		prm_streak = 0
		try:
			tm = getattr(self.bot, 'token_manager', None) if self.bot else None
			if tm is not None and hasattr(tm, 'call_with_auto_refresh'):
				data = await tm.call_with_auto_refresh(fn_ka90013, code, dt=dt_yyyymmdd)
			else:
				data = await fn_ka90013(code, token, dt_yyyymmdd)
			if data.get('return_code') == 0:
				items = data.get('stk_daly_prm_trde_trnsn', [])[:5]
				if items:
					prm_values = [_parse_kiwoom_signed(it.get('prm_netprps_amt', '')) for it in items]
					prm_net_mm_today = prm_values[0]
					prm_streak = _calc_streak(prm_values)
		except Exception:
			logger.exception(f"ka90013 호출 실패 {code}")

		# === 엣지 분석기 통합 (Lee 5/27 지시서 4대 방향) ===
		# ① 억원 정규화: 모든 수급 데이터 → (수량 × 종가 / 1억)
		frgnr_eok = qty_thousand_to_eok(frgnr_today, close_price)
		orgn_eok = qty_thousand_to_eok(orgn_today, close_price)
		trust_eok = qty_thousand_to_eok(trust_today, close_price)
		pension_eok = qty_thousand_to_eok(pension_today, close_price)
		# 프로그램은 ka90013 응답이 이미 백만원 단위 → 억원 변환
		program_eok = program_amount_mm_to_eok(prm_net_mm_today)

		# ② 스마트머니 (투신 + 연기금) 합산 — edge_score 가중치 입력
		smart_money_eok = trust_eok + pension_eok

		# ④ edge_score 4대 매트릭스 가중치
		edge_score = calculate_edge_score(
			price_location=price_location,
			smart_money_eok=smart_money_eok,
			trust_days=trust_streak if trust_streak > 0 else 0,
			pension_days=pension_streak if pension_streak > 0 else 0,
			cntr_str_5min=cntr_str_5min if cntr_str_5min is not None else 0.0,
			program_eok=program_eok,
		)

		return {
			'code': code,
			'name': name,
			'volume_ratio': vol_ratio,  # ka10059 acc_trde_qty 기반
			'hit_count': pool_entry.get('hit_count', 0),
			'seq_ids': pool_entry.get('seq_ids', []),
			'cntr_str_5min': cntr_str_5min,
			# 외인/기관/개인 (천주 raw + 억원 정규화)
			'frgnr_today': frgnr_today,
			'orgn_today': orgn_today,
			'ind_today': ind_today,
			'frgnr_streak': frgnr_streak,
			'orgn_streak': orgn_streak,
			'frgnr_eok': round(frgnr_eok, 2),
			'orgn_eok': round(orgn_eok, 2),
			# 투신/연기금 분리 (5/27 신규)
			'trust_today': trust_today,
			'pension_today': pension_today,
			'trust_streak': trust_streak,
			'pension_streak': pension_streak,
			'trust_eok': round(trust_eok, 2),
			'pension_eok': round(pension_eok, 2),
			'smart_money_eok': round(smart_money_eok, 2),
			# 프로그램
			'prm_net_mm_today': prm_net_mm_today,
			'prm_streak': prm_streak,
			'program_eok': round(program_eok, 2),
			# 종가 위치 + 종가
			'close_price': int(close_price),
			'price_location': round(price_location, 1),
			# 엣지 점수 (0~100)
			'edge_score': edge_score,
		}

	# ─────────────────────────────────────────────────────────
	# 시장 환경 수집
	# ─────────────────────────────────────────────────────────
	async def _gather_market_info(self) -> dict:
		"""KOSPI/KOSDAQ 당일 등락률."""
		from tools.data_loaders import load_market_change

		today = datetime.now().strftime('%Y-%m-%d')
		try:
			kospi_chg, kosdaq_chg = load_market_change(today)
			return {
				'kospi_chg_pct': float(kospi_chg),
				'kosdaq_chg_pct': float(kosdaq_chg),
			}
		except Exception:
			logger.exception("시장 환경 수집 실패")
			return {}

	# ─────────────────────────────────────────────────────────
	# 텔레그램 포맷
	# ─────────────────────────────────────────────────────────
	def _format_telegram(self, results: list, market: dict, today: str) -> list:
		"""분석 결과 → 텔레그램 메시지 (길면 여러 건으로 분할).

		정렬: edge_score ↓ → 체결강도 ↓ → 외인 streak |abs| ↓ (5/27 엣지 분석기 통합).
		70점 이상 종목은 next_day_watchlist.json 별도 저장.
		"""
		def sort_key(r):
			return (
				-((r.get('edge_score') or 0)),
				-((r.get('cntr_str_5min') or 0)),
				-abs(r.get('frgnr_streak') or 0),
			)

		valid = [r for r in results if 'error' not in r]
		errors = [r for r in results if 'error' in r]
		valid.sort(key=sort_key)

		frgnr_strong = sum(1 for r in valid if (r.get('frgnr_streak') or 0) >= FRGNR_STREAK_HIGHLIGHT)
		pick_count = sum(1 for r in valid if (r.get('edge_score') or 0) >= EDGE_SCORE_PICK_CUT)

		# 70점 이상 자동 watchlist 저장
		self._save_next_day_watchlist(valid, today)

		header_lines = [
			f"📊 [{today} 일일 분석] 매칭 {len(results)}종목 (정상 {len(valid)}, 데이터부족 {len(errors)})",
		]
		if pick_count > 0:
			header_lines.append(f"🎯 엣지 {EDGE_SCORE_PICK_CUT}점 이상 (pick 후보): {pick_count}종목")
		if frgnr_strong > 0:
			header_lines.append(f"⭐ 외인 +{FRGNR_STREAK_HIGHLIGHT}일 이상 강세: {frgnr_strong}종목")
		lines = ['\n'.join(header_lines)]

		count = 0
		for r in valid:
			if count >= TOP_N_DISPLAY:
				lines.append(f"...외 {len(valid) - TOP_N_DISPLAY}종목 생략")
				break
			lines.append(self._format_one(r))
			count += 1

		if errors:
			lines.append("\n⚠️ 데이터 부족: " + ", ".join(e['code'] for e in errors[:10]))

		if market:
			kospi = market.get('kospi_chg_pct')
			kosdaq = market.get('kosdaq_chg_pct')
			if kospi is not None and kosdaq is not None:
				lines.append(f"\n📈 [시장] KOSPI {kospi:+.2f}% / KOSDAQ {kosdaq:+.2f}%")
		else:
			lines.append("\n⚠️ 시장 환경 데이터 수집 실패")

		lines.append("\n👉 pick <code1> <code2> ... 로 매수 후보 확정")

		# 메시지 분할 (Telegram 4096 한도)
		messages = []
		current = ""
		for line in lines:
			if len(current) + len(line) + 1 > TELEGRAM_MAX_CHARS:
				if current:
					messages.append(current)
				current = line
			else:
				current = (current + "\n" + line) if current else line
		if current:
			messages.append(current)
		return messages

	def _save_next_day_watchlist(self, valid_results: list, today: str):
		"""엣지 70점 이상 종목 → config/data/next_day_watchlist.json 자동 저장.

		Lee 지시서 ④: edge_score ≥ 70 자동 적재. Lee가 pick 결정 시 참고용.
		영구 원칙 (#30): 봇 데몬 내부에서만 수정.
		"""
		import json
		import os

		picks = [r for r in valid_results if (r.get('edge_score') or 0) >= EDGE_SCORE_PICK_CUT]
		base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
		path = os.path.join(base_dir, 'config', 'data', 'next_day_watchlist.json')

		try:
			os.makedirs(os.path.dirname(path), exist_ok=True)
			payload = {
				'date': today,
				'generated_at': datetime.now().isoformat(timespec='seconds'),
				'pick_cut': EDGE_SCORE_PICK_CUT,
				'count': len(picks),
				'picks': [
					{
						'code': p.get('code'),
						'name': p.get('name'),
						'edge_score': p.get('edge_score'),
						'price_location': p.get('price_location'),
						'cntr_str_5min': p.get('cntr_str_5min'),
						'volume_ratio': p.get('volume_ratio'),
						'close_price': p.get('close_price'),
						'smart_money_eok': p.get('smart_money_eok'),
						'trust_eok': p.get('trust_eok'),
						'pension_eok': p.get('pension_eok'),
						'trust_streak': p.get('trust_streak'),
						'pension_streak': p.get('pension_streak'),
						'frgnr_eok': p.get('frgnr_eok'),
						'frgnr_streak': p.get('frgnr_streak'),
						'program_eok': p.get('program_eok'),
					}
					for p in picks
				],
			}
			tmp = path + '.tmp'
			with open(tmp, 'w', encoding='utf-8') as f:
				json.dump(payload, f, ensure_ascii=False, indent=2)
			os.replace(tmp, path)
			logger.info(f"[daily_analyzer] next_day_watchlist 저장: {len(picks)}종목 (edge≥{EDGE_SCORE_PICK_CUT})")
		except Exception:
			logger.exception("[daily_analyzer] next_day_watchlist 저장 실패")

	def _format_one(self, r: dict) -> str:
		"""종목 1건 포맷 — 체결강도/외인기관/프로그램 + 거래량비 (일봉 의존 부분 [폐기])."""
		# [폐기] 일봉 의존 변수:
		# chg = r.get('today_chg_pct') or 0
		# chg_sym = '🟥' if chg > 0 else '🟦' if chg < 0 else '⬜'
		# bull = '🟢' if r.get('is_bullish') else '🔴'
		# close = int(r.get('today_close') or 0)
		name = r.get('name') or '-'
		code = r.get('code', '-')
		cntr_str = r.get('cntr_str_5min')
		cntr_str_s = f"{cntr_str:.0f}" if cntr_str is not None else "-"
		vol_ratio = r.get('volume_ratio')
		vol_ratio_s = f"{vol_ratio:.0f}%" if vol_ratio is not None else "-"

		highlight = ''
		if (r.get('frgnr_streak') or 0) >= FRGNR_STREAK_HIGHLIGHT:
			highlight = ' ⭐'

		# streak 표시: +3일 / -2일 / 0
		def fmt_streak(s):
			if s > 0:
				return f"+{s}일"
			if s < 0:
				return f"{s}일"
			return "0"

		# 천주 표시: +24,091천주 / -13,110천주 / 0
		def fmt_thousand(v):
			if v == 0:
				return '0'
			return f"{v:+,}천주"

		# 엣지 분석기 신규 필드
		edge = r.get('edge_score', 0)
		ploc = r.get('price_location')
		ploc_s = f"{ploc:.0f}%" if ploc is not None else "-"
		trust_eok = r.get('trust_eok', 0)
		pension_eok = r.get('pension_eok', 0)
		smart_eok = r.get('smart_money_eok', 0)
		program_eok = r.get('program_eok', 0)

		# 엣지 점수 70+ 별표 추가
		pick_mark = ' 🎯' if edge >= EDGE_SCORE_PICK_CUT else ''

		return (
			f"📍 {name} ({code}){highlight}{pick_mark}\n"
			f"   ⭐ 엣지 {edge}점 / 종가 {ploc_s} / 체결강도 {cntr_str_s} / 거래량비 {vol_ratio_s}\n"
			f"   💰 외인 {fmt_streak(r.get('frgnr_streak') or 0)} / "
			f"기관 {fmt_streak(r.get('orgn_streak') or 0)} / "
			f"투신 {fmt_streak(r.get('trust_streak') or 0)} / "
			f"연기금 {fmt_streak(r.get('pension_streak') or 0)} / "
			f"프로그램 {fmt_streak(r.get('prm_streak') or 0)}\n"
			f"   📦 스마트머니 {smart_eok:+,.1f}억 "
			f"(투신 {trust_eok:+,.1f} / 연기금 {pension_eok:+,.1f}) / "
			f"프로그램 {program_eok:+,.1f}억"
		)
