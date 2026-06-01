"""② 단일ETF 자금흐름 collector — 14종 ka10081 합산 (기초종목 귀속).

spec v3 §1: 가중 20%.
spec v3 §4: 자체 키움 API (일봉 거래대금).
spec v3 §3: ETF_TO_UNDERLYING 하드코딩 (패턴 매칭 금지).

단위:
  ka10081 trde_prica는 **백만원** 단위 (Phase 0b 검증).
  내부 저장은 **원** 단위 (× 1,000,000).
  → 005930 합산 = 7종 거래대금(원) 총합

영구 원칙 #30: 봇 데몬 내부에서만 실행.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# trde_prica 단위 변환 (백만원 → 원)
TRDE_PRICA_TO_WON = 1_000_000


def aggregate_etf_flows(etf_to_candles: dict, base_dt: str) -> dict:
	"""{etf_code: candles 리스트} → 기초종목별 거래대금 합산.

	Args:
		etf_to_candles: {'491220': [{date, trade_amount, ...}, ...], ...}
		base_dt: 일치 시킬 일자 (YYYYMMDD). 해당 일자의 candle만 합산.

	Returns: {
	  '005930': {'etf_flow': int(원), 'etfs_count': int, 'etfs': [{code, trade_amount_won}, ...]},
	  '000660': {...}
	}
	  - etf_flow: 합산 거래대금 (원). 일치 candle 없는 ETF는 합산 제외.
	  - etfs_count: 합산에 포함된 ETF 수 (최대 7).
	"""
	from ..etf_mapping import ETF_TO_UNDERLYING, TARGET_UNDERLYINGS

	by_underlying = {
		u: {'etf_flow': 0, 'etfs_count': 0, 'etfs': []}
		for u in TARGET_UNDERLYINGS
	}

	for code, candles in etf_to_candles.items():
		if not candles:
			continue
		# base_dt와 일치하는 candle (보통 [0])
		match = None
		for c in candles:
			if c.get('date') == base_dt:
				match = c
				break
		if match is None:
			continue

		underlying = ETF_TO_UNDERLYING.get(code)
		if underlying not in by_underlying:
			continue

		amount_won = int(match.get('trade_amount', 0)) * TRDE_PRICA_TO_WON
		by_underlying[underlying]['etf_flow'] += amount_won
		by_underlying[underlying]['etfs_count'] += 1
		by_underlying[underlying]['etfs'].append({
			'code': code,
			'date': match['date'],
			'trade_amount_won': amount_won,
		})

	return by_underlying


async def collect_etf_flows(base_dt: str, token: str) -> dict:
	"""14종 ETF 일봉 거래대금 조회 + 기초종목별 합산.

	Args:
		base_dt: 조회 기준일 YYYYMMDD (가장 최근 영업일)
		token: 키움 API 토큰

	Returns: {
	  '005930': {'etf_flow', 'etfs_count', 'etfs'},
	  '000660': {...},
	  'fetched_count': int (응답 받은 ETF 수, 최대 14)
	}
	"""
	from api.daily_candle import fn_ka10081
	from ..etf_mapping import ETF_TO_UNDERLYING

	etf_codes = list(ETF_TO_UNDERLYING.keys())

	# 동시 호출 — rate limiter가 자동 직렬화 (0.3초 간격)
	results = await asyncio.gather(
		*[
			fn_ka10081(c, base_dt=base_dt, token=token, silent=True)
			for c in etf_codes
		],
		return_exceptions=True,
	)

	# {etf_code: candles}로 정규화
	etf_to_candles: dict = {}
	fetched_count = 0
	for code, r in zip(etf_codes, results):
		if isinstance(r, Exception):
			logger.warning(f"[etf_flow] {code} 호출 예외: {type(r).__name__}")
			etf_to_candles[code] = []
			continue
		if not isinstance(r, dict) or r.get('return_code') != 0:
			logger.warning(f"[etf_flow] {code} rc={r.get('return_code') if isinstance(r, dict) else 'N/A'}")
			etf_to_candles[code] = []
			continue
		candles = r.get('candles', [])
		etf_to_candles[code] = candles
		if candles:
			fetched_count += 1

	# 기초종목별 집계
	by_underlying = aggregate_etf_flows(etf_to_candles, base_dt)
	by_underlying['fetched_count'] = fetched_count

	logger.info(
		f"[etf_flow] {base_dt} fetched={fetched_count}/{len(etf_codes)} "
		f"005930={by_underlying.get('005930', {}).get('etf_flow', 0):,}원 "
		f"000660={by_underlying.get('000660', {}).get('etf_flow', 0):,}원"
	)
	return by_underlying
