"""semi_trigger 1년치 과거 데이터 백필 — Phase 6a.

매일 5축 raw 값을 daily_factors 테이블에 저장. 이후 backtest 엔진이 walk-forward
방식으로 z-score + semi_score + trigger 시뮬레이션.

데이터 소스 (1년 기준 ≈ 252 영업일):
  yfinance history(period='1y'): MU/WDC/SNDK/STX + ^SOX/NVDA + KRW=X
  ka10081 (1회 호출): 14 ETF + 005930/000660 (415일치 응답 — 1년 충분)
  ka10059 페이징: 005930/000660 외인 일별 (100일/page → 3페이지 ~300일)

영구 원칙 #30: 봇 데몬 내부에서만 실행.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_ka10059_year(stk_cd: str, end_dt: str, token: str,
                              target_days: int = 252,
                              min_date: str = '20240101') -> list:
	"""ka10059 dt 분할 호출 — 100일치씩 거슬러 올라가며 ~target_days 수집.

	키움 cont_yn 페이징이 작동 안 함 (next_key 미반환) → dt 파라미터에 옛 날짜를
	넣으면 그 시점 기준 직전 100일 응답. 가장 오래된 일자 -1일을 다음 dt로 사용.

	Args:
		stk_cd: 종목코드
		end_dt: 최신 기준일 YYYYMMDD
		token: API 토큰
		target_days: 수집 목표 일수
		min_date: 안전 하한 (이 일자 이전엔 멈춤)

	Returns: [{date: YYYYMMDD, frgnr_invsr: int (천주, 부호유지)}, ...]  date DESC
	"""
	from datetime import datetime, timedelta
	from api.inv_trade_trend import fn_ka10059
	from .collectors.foreign_flow import _parse_signed

	seen: dict = {}
	current_dt = end_dt
	loops = 0
	while len(seen) < target_days and loops < 10:
		loops += 1
		try:
			resp = await fn_ka10059(stk_cd, token, current_dt)
		except Exception:
			logger.exception(f"[backfill] ka10059 dt={current_dt} 호출 실패")
			break
		if not isinstance(resp, dict) or resp.get('return_code') != 0:
			logger.warning(f"[backfill] ka10059 rc={resp.get('return_code') if isinstance(resp, dict) else 'N/A'}")
			break
		items = resp.get('stk_invsr_orgn', [])
		if not items:
			break

		new_count = 0
		for it in items:
			date = str(it.get('dt', '')).strip()
			if not date or date in seen:
				continue
			seen[date] = {
				'date':        date,
				'frgnr_invsr': _parse_signed(it.get('frgnr_invsr', '')),
			}
			new_count += 1
		if new_count == 0:
			break  # 더 못 가져옴

		# 가장 오래된 일자 -1일 → 다음 dt
		oldest = min(seen.keys())
		try:
			d = datetime.strptime(oldest, '%Y%m%d') - timedelta(days=1)
			current_dt = d.strftime('%Y%m%d')
		except Exception:
			break
		if current_dt < min_date:
			break
		# rate limit 보호
		await asyncio.sleep(0.5)

	result = sorted(seen.values(), key=lambda x: x['date'], reverse=True)
	logger.info(f"[backfill] ka10059 {stk_cd} dt-split 수집 {len(result)}일 (loops={loops})")
	return result


def aggregate_foreign_5d_history(daily_items: list, close_by_date: dict) -> dict:
	"""외인 일별 → 각 일자별 5일 누적 (원).

	Args:
		daily_items: [{date: YYYYMMDD, frgnr_invsr: 천주}, ...] (date DESC)
		close_by_date: {YYYYMMDD: 종가(원)} (ka10081 candles에서 추출)

	Returns: {YYYYMMDD: 5d 누적(원), ...}
	  각 일자 d 기준: items[i..i+4]의 frgnr_invsr 합 × 1000 × close[d].
	  5일 미만이면 가용 일수로 계산.
	"""
	out = {}
	for i, base_item in enumerate(daily_items):
		date = base_item['date']
		close = close_by_date.get(date, 0)
		if close <= 0:
			continue
		# 그날 + 직전 4일 합 (총 5일)
		window = daily_items[i:i + 5]
		total_qty = sum(x.get('frgnr_invsr', 0) for x in window)
		out[date] = int(total_qty * 1000 * close)
	return out


def aggregate_etf_flow_history(etf_to_candles: dict) -> dict:
	"""ETF 14종 일별 거래대금 → 기초종목별 일자별 합산 (원).

	Args:
		etf_to_candles: {etf_code: [{date, trade_amount(백만원), close, ...}, ...]}

	Returns: {underlying: {date: total_flow(원), ...}}
	"""
	from .etf_mapping import ETF_TO_UNDERLYING, TARGET_UNDERLYINGS
	from .collectors.etf_flow import TRDE_PRICA_TO_WON

	by_underlying = {u: {} for u in TARGET_UNDERLYINGS}
	for etf_code, candles in etf_to_candles.items():
		underlying = ETF_TO_UNDERLYING.get(etf_code)
		if underlying not in by_underlying:
			continue
		for c in candles or []:
			date = c.get('date')
			if not date:
				continue
			amount_won = int(c.get('trade_amount', 0)) * TRDE_PRICA_TO_WON
			by_underlying[underlying][date] = (
				by_underlying[underlying].get(date, 0) + amount_won
			)
	return by_underlying


def to_iso_date(yyyymmdd: str) -> str:
	"""YYYYMMDD → YYYY-MM-DD."""
	if not yyyymmdd or len(yyyymmdd) < 8:
		return ''
	return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


async def backfill_factors(end_dt: str, token: str,
                            days_target: int = 252,
                            db_path: Optional[str] = None) -> dict:
	"""1년치 (252영업일) 과거 5축 raw 값을 daily_factors 테이블에 저장.

	Args:
		end_dt: 최신 기준일 YYYYMMDD (오늘 또는 가장 최근 영업일)
		token: 키움 API 토큰
		days_target: 백필 목표 일수 (기본 252)
		db_path: DB 경로 override (테스트용)

	Returns: {
	  'saved_days': int,         # 저장한 일자 수 (per stock)
	  'sources': {'us_memory': N일, 'etf_flow': ..., 'fx': ..., 'foreign_flow': ...},
	  'us_memory_dates': set,    # 디버그용
	}
	"""
	from . import db as st_db
	from .etf_mapping import ETF_TO_UNDERLYING, TARGET_UNDERLYINGS
	from .collectors.us_memory import calc_us_memory
	from api.external_index import (
		fetch_history, US_MEMORY_SYMBOLS, SYM_SOX, SYM_NVDA, SYM_MU,
		SYM_USDKRW, SYM_NQ,
	)
	from api.daily_candle import fn_ka10081

	logger.info(f"[backfill] start end_dt={end_dt} target={days_target}일")

	# 1) yfinance 1년치 history — US 메모리 4종 + 보조 (SOX/NVDA/MU 포함) + FX + NQ
	yf_symbols = list(US_MEMORY_SYMBOLS) + [SYM_SOX, SYM_NVDA, SYM_USDKRW, SYM_NQ]
	yf_results = await asyncio.gather(
		*[fetch_history(s, period='1y') for s in yf_symbols]
	)
	yf_history = dict(zip(yf_symbols, yf_results))
	logger.info(f"[backfill] yfinance 수집 {[len(h) for h in yf_results]}")

	# us_memory 일자별 평균
	all_dates_set = set()
	for s in US_MEMORY_SYMBOLS:
		all_dates_set.update(yf_history.get(s, {}).keys())
	us_memory_by_date = {}
	for d in all_dates_set:
		symbol_to_pct = {
			s: yf_history.get(s, {}).get(d) for s in US_MEMORY_SYMBOLS
		}
		us_memory_by_date[d] = calc_us_memory(symbol_to_pct)

	fx_by_date = yf_history.get(SYM_USDKRW, {})
	sox_by_date = yf_history.get(SYM_SOX, {})
	nvda_by_date = yf_history.get(SYM_NVDA, {})
	nq_by_date = yf_history.get(SYM_NQ, {})

	# 2) ka10081 — 14 ETF + 2 underlying 일봉 (1년치 ~415일 응답 충분)
	etf_codes = list(ETF_TO_UNDERLYING.keys())
	all_codes = etf_codes + list(TARGET_UNDERLYINGS)
	candle_results = await asyncio.gather(
		*[fn_ka10081(c, base_dt=end_dt, token=token, silent=True) for c in all_codes],
		return_exceptions=True,
	)
	code_to_candles = {}
	for c, r in zip(all_codes, candle_results):
		if isinstance(r, Exception):
			code_to_candles[c] = []
			continue
		if not isinstance(r, dict) or r.get('return_code') != 0:
			code_to_candles[c] = []
			continue
		code_to_candles[c] = r.get('candles', [])

	# 기초종목 close map (date YYYYMMDD → close 원)
	close_005930 = {c['date']: c['close'] for c in code_to_candles.get('005930', [])}
	close_000660 = {c['date']: c['close'] for c in code_to_candles.get('000660', [])}

	# ETF 일자별 거래대금 합산
	etf_candles = {c: code_to_candles[c] for c in etf_codes}
	etf_flow_by_underlying = aggregate_etf_flow_history(etf_candles)

	# 3) ka10059 — 외인 일별 (페이징)
	foreign_005930_items = await fetch_ka10059_year('005930', end_dt, token)
	foreign_000660_items = await fetch_ka10059_year('000660', end_dt, token)

	# 외인 5일 누적 (per date)
	foreign_5d_005930 = aggregate_foreign_5d_history(foreign_005930_items, close_005930)
	foreign_5d_000660 = aggregate_foreign_5d_history(foreign_000660_items, close_000660)

	# 4) 일자별 통합 — us_memory 날짜 기준 (한국과 미국 시차 1일)
	# 한국 영업일 d 시점에 us_memory는 직전 미국 거래일 데이터 반영. 단순화 — d 동일.
	# yfinance는 미국 시각 ISO 일자라 한국 매핑 시 -1일 또는 동일일 처리 필요.
	# 백테스트 sanity: 같은 ISO 일자로 매핑 (보수적).
	saved_per_stock = {u: 0 for u in TARGET_UNDERLYINGS}

	# 6) 종목별 4 sub-signal history (ka10081 candles + ka90013 프로그램 일별)
	from .collectors.stock_factors import (
		calc_price_change, calc_volume_ratio, MILLION,
	)
	from .collectors.foreign_flow import _parse_signed
	from api.program_trade import fn_ka90013

	# 005930/000660 ka90013 일별 프로그램 매매 (단일 호출 → 100일치 + 페이징)
	# 단순화: dt-split 페이징 비슷하게 또는 1회만 (rate limit 부담 감소)
	program_history = {}  # {stock: {date(YYYYMMDD): net_won}}
	for stock_code in TARGET_UNDERLYINGS:
		net_by_date = {}
		current_dt_p = end_dt
		for _ in range(3):  # 최대 3회 호출 (~300일)
			try:
				resp = await fn_ka90013(stock_code, token, current_dt_p)
			except Exception:
				break
			if not isinstance(resp, dict) or resp.get('return_code') != 0:
				break
			items = resp.get('stk_daly_prm_trde_trnsn', [])
			if not items:
				break
			new_count = 0
			for it in items:
				d = str(it.get('dt', '')).strip()
				if not d or d in net_by_date:
					continue
				net_mm = _parse_signed(it.get('prm_netprps_amt', ''))
				net_by_date[d] = int(net_mm) * MILLION
				new_count += 1
			if new_count == 0:
				break
			oldest = min(net_by_date.keys())
			from datetime import datetime as _dt2, timedelta as _td2
			try:
				current_dt_p = (_dt2.strptime(oldest, '%Y%m%d') - _td2(days=1)).strftime('%Y%m%d')
			except Exception:
				break
			if current_dt_p < '20240101':
				break
			await asyncio.sleep(0.3)
		program_history[stock_code] = net_by_date
		logger.info(f"[backfill] ka90013 {stock_code} {len(net_by_date)}일")

	# 005930/000660 일자 집합 (한국 영업일)
	for stock_code in TARGET_UNDERLYINGS:
		close_map = close_005930 if stock_code == '005930' else close_000660
		stock_candles = code_to_candles.get(stock_code, [])
		# 일자별 candle map
		stock_candle_by_date = {c['date']: c for c in stock_candles}
		# 일별 거래량 list (오름차순) — volume_ratio 직전 5일 평균용
		sorted_candles_asc = sorted(stock_candles, key=lambda c: c['date'])

		ff_map = foreign_5d_005930 if stock_code == '005930' else foreign_5d_000660
		prog_map = program_history.get(stock_code, {})

		# 키움 일봉 일자 (YYYYMMDD) 기준 — 한국 영업일
		for idx, d_yyyymmdd in enumerate(sorted(close_map.keys(), reverse=True)[:days_target]):
			d_iso = to_iso_date(d_yyyymmdd)
			cur_candle = stock_candle_by_date.get(d_yyyymmdd, {})
			# 직전 거래일 (sorted_candles_asc에서 d_yyyymmdd 위치 -1)
			prev_close_v = 0
			prev_vols_5 = []
			# 직전 인접 거래일 찾기
			for i, c in enumerate(sorted_candles_asc):
				if c['date'] == d_yyyymmdd:
					if i >= 1:
						prev_close_v = sorted_candles_asc[i - 1].get('close', 0)
					# 직전 5일 거래량
					if i >= 1:
						prev_vols_5 = [
							sorted_candles_asc[j].get('volume', 0)
							for j in range(max(0, i - 5), i)
						]
					break

			factors = {
				'us_memory':       us_memory_by_date.get(d_iso),
				'fx_change':       fx_by_date.get(d_iso),
				'foreign_flow_5d': ff_map.get(d_yyyymmdd),
				'nasdaq_futures':  nq_by_date.get(d_iso),
				'sox':             sox_by_date.get(d_iso),
				'nvda':            nvda_by_date.get(d_iso),
				'mu':              yf_history.get(SYM_MU, {}).get(d_iso),
				# 종목별 4 sub-signal
				'price_change':    calc_price_change(cur_candle.get('close', 0), prev_close_v),
				'volume_amount':   (int(cur_candle.get('trade_amount', 0)) * MILLION) if cur_candle.get('trade_amount') else None,
				'volume_ratio':    calc_volume_ratio(cur_candle.get('volume', 0), prev_vols_5),
				'program_net':     prog_map.get(d_yyyymmdd),
			}
			st_db.upsert_factors(d_iso, stock_code, factors, db_path=db_path)
			saved_per_stock[stock_code] += 1

	logger.info(f"[backfill] 완료 saved={saved_per_stock}")
	return {
		'saved_per_stock': saved_per_stock,
		'us_memory_dates': len(us_memory_by_date),
		'etf_count': len(etf_codes),
		'foreign_005930_days': len(foreign_005930_items),
		'foreign_000660_days': len(foreign_000660_items),
	}
