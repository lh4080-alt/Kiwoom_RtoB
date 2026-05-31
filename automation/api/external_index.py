"""미국 지수/종목 외부 데이터 (stick 08:30 pre-market 체크용).

yfinance를 asyncio.to_thread로 감싸서 봇의 asyncio 흐름에 통합.

기본 심볼 (SK하이닉스 등 메모리/HBM 종목 매수 신호 — 3종목 다수결):
  ^SOX  필라델피아 반도체 (섹터 sentiment 전반)
  NVDA  NVIDIA (HBM 수요 driver — AI 모멘텀)
  MU    Micron (DRAM/NAND 직접 경쟁사 — 메모리 사이클 동행)

반환: 등락률 % (현재가 vs 전일 종가)
실패: None
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SYM_SOX = '^SOX'
SYM_NVDA = 'NVDA'
SYM_MU = 'MU'


def _fetch_change_pct_sync(symbol: str) -> Optional[float]:
	"""동기 호출 — yfinance Ticker.fast_info / history fallback.

	Returns: 등락률 % 또는 None (실패).
	"""
	try:
		import yfinance as yf
	except ImportError:
		logger.error("yfinance 미설치 — pip install yfinance")
		return None

	try:
		t = yf.Ticker(symbol)
		# fast_info: 빠른 메타 (last_price + previous_close)
		try:
			fi = t.fast_info
			last = fi.get('last_price') if isinstance(fi, dict) else getattr(fi, 'last_price', None)
			prev = fi.get('previous_close') if isinstance(fi, dict) else getattr(fi, 'previous_close', None)
			if last is not None and prev is not None and prev > 0:
				return (float(last) - float(prev)) / float(prev) * 100.0
		except Exception:
			pass

		# fallback: 최근 2일 일봉
		hist = t.history(period='5d', interval='1d')
		if hist is not None and len(hist) >= 2:
			closes = hist['Close'].dropna()
			if len(closes) >= 2:
				prev_c = float(closes.iloc[-2])
				last_c = float(closes.iloc[-1])
				if prev_c > 0:
					return (last_c - prev_c) / prev_c * 100.0
	except Exception:
		logger.exception(f"yfinance fetch 실패: {symbol}")
	return None


async def fetch_change_pct(symbol: str) -> Optional[float]:
	"""비동기 래퍼 — to_thread로 yfinance 호출 격리."""
	return await asyncio.to_thread(_fetch_change_pct_sync, symbol)


async def fetch_semi_trio() -> dict:
	"""SOX + NVDA + MU 등락률 동시 조회. 각 실패 시 None.

	Returns: {'sox': float | None, 'nvda': float | None, 'mu': float | None}
	"""
	sox, nvda, mu = await asyncio.gather(
		fetch_change_pct(SYM_SOX),
		fetch_change_pct(SYM_NVDA),
		fetch_change_pct(SYM_MU),
		return_exceptions=False,
	)
	return {'sox': sox, 'nvda': nvda, 'mu': mu}
