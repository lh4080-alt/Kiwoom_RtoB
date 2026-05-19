"""
16:00 일일 분석 + 텔레그램 알림.

수집풀(collection_pool.json) 모든 종목을 자동 분석한 결과를 텔레그램으로 보고하고,
수집풀과 매수 대기열(buy_queue.json)을 비운다.

Phase 2 Step B: 기존 자원(load_7d_bars / load_market_change / get_stock_name) 활용.
체결강도/외인기관 정보는 Step C에서 KiwoomClient 완성 후 추가 예정.

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


class DailyAnalyzer:
	"""16:00 일일 분석. 수집풀 종목별 정보 + 시장 환경 → 텔레그램 알림."""

	def __init__(self, bot_ref=None):
		"""
		Args:
			bot_ref: ChatCommand 또는 봇 객체 (현재는 self.daily_analyzer.run() 호출만 받음).
		"""
		self.bot = bot_ref

	async def run(self):
		"""16:00 트리거 진입점. 분석 + 알림 + 풀 정리."""
		from utils.collection_pool import get_pool, clear_pool, get_stock_name
		from utils.buy_queue import clear_queue
		from telegram.tel_send import tel_send

		today = datetime.now().strftime('%Y-%m-%d')
		logger.info(f"daily_analyzer 시작: {today}")

		try:
			pool = get_pool()
			codes = list(pool.keys()) if isinstance(pool, dict) else []

			if not codes:
				await tel_send(f"[{today} 일일 분석] 오늘 매칭 종목 없음")
				logger.info("수집풀 비어있음 — 분석 스킵")
			else:
				logger.info(f"daily_analyzer 분석 대상: {len(codes)}건")
				results = []
				for code in codes:
					try:
						info = await self._gather_stock_info(code, pool.get(code, {}))
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

	# ─────────────────────────────────────────────────────────
	# 종목별 정보 수집 (Step B: 기존 자원만 사용)
	# ─────────────────────────────────────────────────────────
	async def _gather_stock_info(self, code: str, pool_entry: dict) -> dict:
		"""단일 종목 정보. \\beelink\\market_data 일봉 + 종목명 캐시 사용."""
		from tools.data_loaders import load_7d_bars
		from utils.collection_pool import get_stock_name

		name = await get_stock_name(code)

		bars = load_7d_bars(code)
		if bars is None or len(bars) < 2:
			return {
				'code': code,
				'name': name,
				'error': 'no_bars',
				'hit_count': pool_entry.get('hit_count', 0),
				'seq_ids': pool_entry.get('seq_ids', []),
			}

		today_row = bars.iloc[-1]
		prev_row = bars.iloc[-2]

		today_close = float(today_row['close'])
		prev_close = float(prev_row['close'])
		chg_pct = ((today_close - prev_close) / prev_close * 100) if prev_close else 0.0

		# 거래량 비율 (당일 vs 6일 평균)
		today_vol = float(today_row['volume'])
		prev_vols = bars.iloc[:-1]['volume'].astype(float).tolist()
		avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0.0
		vol_ratio = (today_vol / avg_vol * 100) if avg_vol else 0.0

		# 당일 양봉/음봉
		today_open = float(today_row['open'])
		today_high = float(today_row['high'])
		today_low = float(today_row['low'])
		is_bullish = today_close > today_open
		# 종가 위치 (저가 0% ~ 고가 100%)
		rng = today_high - today_low
		close_pos = ((today_close - today_low) / rng * 100) if rng else 50.0

		return {
			'code': code,
			'name': name,
			'today_close': today_close,
			'today_chg_pct': chg_pct,
			'today_vol': int(today_vol),
			'volume_ratio': vol_ratio,
			'is_bullish': is_bullish,
			'close_pos': close_pos,
			'hit_count': pool_entry.get('hit_count', 0),
			'seq_ids': pool_entry.get('seq_ids', []),
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
		"""분석 결과 → 텔레그램 메시지 (길면 여러 건으로 분할)."""
		# 정렬: 거래량 비율 ↓, 등락률 ↓
		def sort_key(r):
			return (-(r.get('volume_ratio') or 0), -(r.get('today_chg_pct') or 0))

		valid = [r for r in results if 'error' not in r]
		errors = [r for r in results if 'error' in r]
		valid.sort(key=sort_key)

		header = f"📊 [{today} 일일 분석] 매칭 {len(results)}종목 (정상 {len(valid)}, 데이터부족 {len(errors)})\n"

		lines = [header]
		count = 0
		for r in valid:
			if count >= TOP_N_DISPLAY:
				lines.append(f"...외 {len(valid) - TOP_N_DISPLAY}종목 생략")
				break
			chg = r['today_chg_pct']
			chg_sym = '🟥' if chg > 0 else '🟦' if chg < 0 else '⬜'
			bull = '🟢' if r['is_bullish'] else '🔴'
			seq_str = ",".join(r['seq_ids']) if r['seq_ids'] else "-"
			lines.append(
				f"{chg_sym} {r['name']} ({r['code']}) {chg:+.2f}% "
				f"종가 {int(r['today_close']):,}원\n"
				f"   {bull} 거래량비 {r['volume_ratio']:.0f}% / "
				f"종가위치 {r['close_pos']:.0f}% / "
				f"hit {r['hit_count']} / seq [{seq_str}]"
			)
			count += 1

		if errors:
			lines.append("\n⚠️ 데이터 부족: " + ", ".join(e['code'] for e in errors[:10]))

		# 시장 환경
		if market:
			kospi = market.get('kospi_chg_pct')
			kosdaq = market.get('kosdaq_chg_pct')
			if kospi is not None and kosdaq is not None:
				lines.append(
					f"\n📈 [시장] KOSPI {kospi:+.2f}% / KOSDAQ {kosdaq:+.2f}%"
				)
		else:
			lines.append("\n⚠️ 시장 환경 데이터 수집 실패")

		lines.append("\n👉 pick <code1> <code2> ... 로 매수 후보 확정")
		lines.append("(실제 09:00 매수는 Step C 이후)")

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
