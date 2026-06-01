"""종목별 4 sub-signal collector — 주가/거래대금/거래량변화율/프로그램.

Lee 6/2 결정: etf_flow 20% → 종목별 4신호 각 5%.

축:
  price_change   ka10081 cur_prc / prev → 일별 등락률 %
  volume_amount  ka10081 trde_prica × 1,000,000 → 원
  volume_ratio   today_volume / mean(prev 5d volume) × 100 → %
  program_net    ka90013 prm_netprps_amt × 1,000,000 → 원

영구 원칙 #30: 봇 데몬 내부에서만 실행.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 백만원 → 원
MILLION = 1_000_000


def calc_price_change(today_close: float, prev_close: float) -> Optional[float]:
	"""일별 등락률 % = (today − prev) / prev × 100. prev<=0이면 None."""
	if not prev_close or prev_close <= 0 or today_close is None:
		return None
	return (today_close - prev_close) / prev_close * 100.0


def calc_volume_ratio(today_volume: int, prev_volumes: list) -> Optional[float]:
	"""거래량 변화율 % = today / mean(prev) × 100. prev 0 또는 빈 리스트면 None."""
	prev_clean = [v for v in (prev_volumes or []) if v and v > 0]
	if not prev_clean or today_volume is None:
		return None
	avg = sum(prev_clean) / len(prev_clean)
	if avg <= 0:
		return None
	return today_volume / avg * 100.0


def extract_stock_factors_from_candles(candles: list,
                                        prev_days: int = 5) -> dict:
	"""ka10081 candles → price_change + volume_amount + volume_ratio 산출.

	Args:
		candles: [{date, open, high, low, close, volume, trade_amount}, ...] (date DESC)
		prev_days: 거래량 변화율 baseline 일수 (직전 5일)

	Returns: {'price_change', 'volume_amount', 'volume_ratio'}
	"""
	if not candles:
		return {'price_change': None, 'volume_amount': None, 'volume_ratio': None}
	cur = candles[0]
	today_close = cur.get('close', 0)
	today_volume = cur.get('volume', 0)
	today_trade_amt = cur.get('trade_amount', 0)

	prev_close = candles[1].get('close', 0) if len(candles) >= 2 else 0
	prev_vols = [c.get('volume', 0) for c in candles[1:1 + prev_days]]

	return {
		'price_change':  calc_price_change(today_close, prev_close),
		'volume_amount': int(today_trade_amt) * MILLION if today_trade_amt else None,
		'volume_ratio':  calc_volume_ratio(today_volume, prev_vols),
	}


async def collect_stock_factors(stock_code: str, base_dt: str, token: str) -> dict:
	"""종목별 4 sub-signal fetch.

	Args:
		stock_code: 005930 / 000660
		base_dt: 기준일 YYYYMMDD
		token: 키움 토큰

	Returns: {
	  'price_change': float | None  (%),
	  'volume_amount': int | None    (원),
	  'volume_ratio': float | None   (%),
	  'program_net':   int | None    (원, 부호유지),
	}
	"""
	from api.daily_candle import fn_ka10081
	from api.program_trade import fn_ka90013

	# 1) 일봉 6일치 (오늘 + 직전 5일)
	candle_resp, prm_resp = await asyncio.gather(
		fn_ka10081(stock_code, base_dt=base_dt, token=token, silent=True),
		fn_ka90013(stock_code, token, base_dt),
		return_exceptions=True,
	)

	# ka10081 처리
	candle_factors = {'price_change': None, 'volume_amount': None, 'volume_ratio': None}
	if isinstance(candle_resp, dict) and candle_resp.get('return_code') == 0:
		candles = candle_resp.get('candles', [])
		# base_dt 또는 그 이하 일자만 (혹시 미래 일자 응답 방지)
		candles = [c for c in candles if c.get('date', '') <= base_dt]
		candle_factors = extract_stock_factors_from_candles(candles)
	else:
		logger.warning(f"[stock_factors] {stock_code} ka10081 실패")

	# ka90013 처리 (프로그램매매)
	program_net = None
	if isinstance(prm_resp, dict) and prm_resp.get('return_code') == 0:
		items = prm_resp.get('stk_daly_prm_trde_trnsn', [])
		if items:
			from .foreign_flow import _parse_signed
			net_mm = _parse_signed(items[0].get('prm_netprps_amt', ''))
			program_net = int(net_mm) * MILLION
	else:
		logger.warning(f"[stock_factors] {stock_code} ka90013 실패")

	result = {
		'price_change':  candle_factors['price_change'],
		'volume_amount': candle_factors['volume_amount'],
		'volume_ratio':  candle_factors['volume_ratio'],
		'program_net':   program_net,
	}
	logger.info(f"[stock_factors] {stock_code} {result}")
	return result
