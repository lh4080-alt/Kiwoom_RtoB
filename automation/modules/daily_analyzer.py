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
		"""단일 종목 정보 — 체결강도 + 외인기관 + 프로그램매매."""
		# from tools.data_loaders import load_7d_bars  # [폐기] 일봉 의존 로직 비활성
		from utils.collection_pool import get_stock_name
		from api.stk_strength import fn_ka10046
		from api.inv_trade_trend import fn_ka10059
		from api.program_trade import fn_ka90013

		name = await get_stock_name(code)

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

		# 체결강도 (ka10046)
		cntr_str_5min: Optional[float] = None
		try:
			data = await fn_ka10046(code, token)
			if data.get('return_code') == 0:
				items = data.get('cntr_str_tm', [])
				if items:
					cntr_str_5min = _parse_kiwoom_float(items[0].get('cntr_str_5min', ''))
		except Exception:
			logger.exception(f"ka10046 호출 실패 {code}")

		# 외인/기관 일별 5일치 + 거래량비 (ka10059)
		frgnr_today = orgn_today = ind_today = 0
		frgnr_streak = orgn_streak = 0
		vol_ratio: Optional[float] = None
		try:
			data = await fn_ka10059(code, token, dt_yyyymmdd)
			if data.get('return_code') == 0:
				items = data.get('stk_invsr_orgn', [])[:5]
				if items:
					frgnr_values = [_parse_kiwoom_signed(it.get('frgnr_invsr', '')) for it in items]
					orgn_values = [_parse_kiwoom_signed(it.get('orgn', '')) for it in items]
					ind_values = [_parse_kiwoom_signed(it.get('ind_invsr', '')) for it in items]
					frgnr_today = frgnr_values[0]
					orgn_today = orgn_values[0]
					ind_today = ind_values[0]
					frgnr_streak = _calc_streak(frgnr_values)
					orgn_streak = _calc_streak(orgn_values)
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

		# 프로그램매매 일별 5일치 (ka90013)
		prm_net_mm_today = 0
		prm_streak = 0
		try:
			data = await fn_ka90013(code, token, dt_yyyymmdd)
			if data.get('return_code') == 0:
				items = data.get('stk_daly_prm_trde_trnsn', [])[:5]
				if items:
					prm_values = [_parse_kiwoom_signed(it.get('prm_netprps_amt', '')) for it in items]
					prm_net_mm_today = prm_values[0]
					prm_streak = _calc_streak(prm_values)
		except Exception:
			logger.exception(f"ka90013 호출 실패 {code}")

		return {
			'code': code,
			'name': name,
			# [폐기] 일봉 의존 필드:
			# 'today_close': today_close,
			# 'today_chg_pct': chg_pct,
			# 'today_vol': int(today_vol),
			# 'is_bullish': is_bullish,
			# 'close_pos': close_pos,
			'volume_ratio': vol_ratio,  # ka10059 acc_trde_qty 기반 (일봉 의존 X)
			'hit_count': pool_entry.get('hit_count', 0),
			'seq_ids': pool_entry.get('seq_ids', []),
			'cntr_str_5min': cntr_str_5min,
			'frgnr_today': frgnr_today,
			'orgn_today': orgn_today,
			'ind_today': ind_today,
			'prm_net_mm_today': prm_net_mm_today,
			'frgnr_streak': frgnr_streak,
			'orgn_streak': orgn_streak,
			'prm_streak': prm_streak,
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

		정렬: 체결강도 5분 ↓ → 외인 streak |abs| ↓ → 거래량비 ↓
		(거래량비는 ka10059 acc_trde_qty 기반 — 일봉 의존 X)
		"""
		def sort_key(r):
			return (
				-((r.get('cntr_str_5min') or 0)),
				-abs(r.get('frgnr_streak') or 0),
				-((r.get('volume_ratio') or 0)),
			)

		valid = [r for r in results if 'error' not in r]
		errors = [r for r in results if 'error' in r]
		valid.sort(key=sort_key)

		frgnr_strong = sum(1 for r in valid if (r.get('frgnr_streak') or 0) >= FRGNR_STREAK_HIGHLIGHT)

		header_lines = [
			f"📊 [{today} 일일 분석] 매칭 {len(results)}종목 (정상 {len(valid)}, 데이터부족 {len(errors)})",
		]
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

		return (
			f"📍 {name} ({code}){highlight}\n"
			f"   📊 거래량비 {vol_ratio_s} / 체결강도 {cntr_str_s}\n"
			f"   💰 외인 {fmt_streak(r.get('frgnr_streak') or 0)} / "
			f"기관 {fmt_streak(r.get('orgn_streak') or 0)} / "
			f"프로그램 {fmt_streak(r.get('prm_streak') or 0)}\n"
			f"   📦 외인 {fmt_thousand(r.get('frgnr_today') or 0)} / "
			f"기관 {fmt_thousand(r.get('orgn_today') or 0)} / "
			f"프로그램 {_format_program_amount(r.get('prm_net_mm_today') or 0)}"
		)
