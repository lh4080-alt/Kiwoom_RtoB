"""③ 원/달러 일중 변화 collector — yfinance KRW=X.

spec v3 §1: 가중 20%.
spec v3 §5: 키움 미지원 → 자체 yfinance.

산출:
  fx_change = (현재 KRW/USD - 전일 종가) / 전일 종가 × 100
  → external_index.fetch_change_pct가 이미 동일 계산. 그대로 재사용.

영구 원칙 #30: 봇 데몬 내부에서만 호출.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def collect_fx_change() -> dict:
	"""원/달러 등락률 조회.

	Returns: {
	  'fx_change': float | None,  # %
	  'symbol': 'KRW=X',
	}
	"""
	from api.external_index import fetch_change_pct, SYM_USDKRW
	pct = await fetch_change_pct(SYM_USDKRW)
	logger.info(f"[fx] KRW=X change_pct={pct}")
	return {'fx_change': pct, 'symbol': SYM_USDKRW}
