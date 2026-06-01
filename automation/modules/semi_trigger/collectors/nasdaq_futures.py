"""⑤ 나스닥 선물 (NQ=F) collector — yfinance 24h 거래.

spec v3 + Lee 6/2 수정: memory_price (10%) → nasdaq_futures (10%) 교체.

산출 시점: 08:30 KST — 미국 정규장 마감 후 야간 거래까지 반영된 최신 NQ change_pct.
NQ=F는 CME Globex 24시간 거래 → 한국 아침 시점 실시간 가능.

영구 원칙 #30: 봇 데몬 내부에서만 호출.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def collect_nasdaq_futures() -> dict:
	"""NQ=F 등락률 조회.

	Returns: {
	  'nasdaq_futures': float | None,
	  'symbol': 'NQ=F',
	}
	"""
	from api.external_index import fetch_change_pct, SYM_NQ
	pct = await fetch_change_pct(SYM_NQ)
	logger.info(f"[nq_futures] NQ=F change_pct={pct}")
	return {'nasdaq_futures': pct, 'symbol': SYM_NQ}
