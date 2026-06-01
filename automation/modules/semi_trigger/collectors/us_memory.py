"""① 미 메모리 강도 collector — MU/WDC/SNDK/STX 동일가중 일간수익 평균.

spec v3 §1: 가중 40% (semi_score 최대 기여 축).
spec v3 §5: 키움이 해외주식 미지원 → 자체 yfinance.

산출:
  us_memory = mean([MU.change_pct, WDC.change_pct, SNDK.change_pct, STX.change_pct])
              (None 종목은 평균에서 제외, 모두 None이면 None)

영구 원칙 #30: 봇 데몬 내부에서만 호출.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calc_us_memory(symbol_to_pct: dict) -> Optional[float]:
	"""4종목 등락률 dict → 동일가중 평균 (None 제외).

	Args:
		symbol_to_pct: {'MU': 1.5, 'WDC': 2.0, 'SNDK': None, 'STX': 0.5} 형태

	Returns:
		float 평균, 모두 None이면 None.
	"""
	valid = [v for v in symbol_to_pct.values() if v is not None]
	if not valid:
		return None
	return sum(valid) / len(valid)


async def collect_us_memory() -> dict:
	"""MU/WDC/SNDK/STX 등락률 fetch + 평균.

	Returns: {
	  'us_memory': float | None,  # 동일가중 평균
	  'symbols': {'MU': float|None, 'WDC': ..., 'SNDK': ..., 'STX': ...},
	  'fetched_count': int,        # None 아닌 종목 수
	}
	"""
	from api.external_index import fetch_change_pct, US_MEMORY_SYMBOLS

	results = await asyncio.gather(
		*[fetch_change_pct(s) for s in US_MEMORY_SYMBOLS],
		return_exceptions=False,
	)
	symbol_to_pct = dict(zip(US_MEMORY_SYMBOLS, results))
	us_memory = calc_us_memory(symbol_to_pct)
	fetched_count = sum(1 for v in results if v is not None)

	logger.info(f"[us_memory] {symbol_to_pct} → avg={us_memory} "
	            f"({fetched_count}/{len(US_MEMORY_SYMBOLS)})")

	return {
		'us_memory': us_memory,
		'symbols': symbol_to_pct,
		'fetched_count': fetched_count,
	}
