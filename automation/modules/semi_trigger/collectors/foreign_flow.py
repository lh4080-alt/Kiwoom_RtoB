"""④ 외인 5일 누적 collector — ka10059 재활용.

spec v3 §1: 가중 10%.
spec v3 §4: 기존 ka10059 wrapper 재활용 (inv_trade_trend.py).
spec v3 §6.1: foreign_flow_5d 단위 = 원.

산출:
  외인 일별 매수 (천주) × 1000주 × 종가(원) = 일별 매수액(원)
  5일 합산 = foreign_flow_5d (원, 부호 유지)

주의: 키움 모의투자는 외인 데이터를 0으로 반환 (메모리 확인). 실계좌에서만 정상.
영구 원칙 #30: 봇 데몬 내부에서만 호출.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _parse_signed(s) -> int:
	"""ka10059 응답 부호 정수 파싱.

	형식: '--12345' (이중 마이너스 = -12345), '-12345', '+12345', '12345', '' → 0.
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


def aggregate_foreign_flow_5d(stk_invsr_orgn: list, close_price: float,
                               days: int = 5) -> int:
	"""ka10059 응답 stk_invsr_orgn → 5일 누적 외인 순매수 (원).

	Args:
		stk_invsr_orgn: ka10059 응답 리스트 ([0]=최신)
		close_price: 종가 (원)
		days: 누적 일수 (기본 5)

	Returns: 누적 매수액(원). 부호 유지 (음수=순매도).
	close_price <= 0 또는 빈 리스트면 0.
	"""
	if not stk_invsr_orgn or close_price <= 0:
		return 0
	total_qty = sum(
		_parse_signed(it.get('frgnr_invsr', ''))
		for it in stk_invsr_orgn[:days]
	)
	# 천주 × 1000 × close = 원
	return int(total_qty * 1000 * close_price)


async def collect_foreign_flow_5d(stock_code: str, base_dt: str,
                                   token: str) -> Optional[int]:
	"""특정 종목의 외인 5일 누적 순매수 (원) 조회.

	Args:
		stock_code: 6자리 종목코드 (005930 / 000660 등)
		base_dt: 조회 기준일 YYYYMMDD
		token: 키움 API 토큰

	Returns:
		int 5일 누적 매수액(원), 부호 유지.
		조회 실패 / 종가 0 / 빈 응답이면 None.
	"""
	from api.inv_trade_trend import fn_ka10059
	from api.stock_info import fn_ka10001

	# 1) 외인 일별 동시 조회 + 종가
	inv_result, info_result = await asyncio.gather(
		fn_ka10059(stock_code, token, base_dt),
		fn_ka10001(stock_code, token=token, silent=True),
		return_exceptions=True,
	)

	# 외인 응답 검증
	if isinstance(inv_result, Exception):
		logger.exception(f"[foreign_flow] {stock_code} ka10059 호출 예외")
		return None
	if not isinstance(inv_result, dict) or inv_result.get('return_code') != 0:
		logger.warning(f"[foreign_flow] {stock_code} ka10059 rc={inv_result.get('return_code') if isinstance(inv_result, dict) else 'N/A'}")
		return None
	items = inv_result.get('stk_invsr_orgn', [])
	if not items:
		logger.warning(f"[foreign_flow] {stock_code} 외인 데이터 비어있음")
		return None

	# 종가 검증
	if isinstance(info_result, Exception) or not isinstance(info_result, dict):
		logger.warning(f"[foreign_flow] {stock_code} 종가 조회 실패")
		return None
	close = abs(float(info_result.get('cur_prc') or 0))
	if close <= 0:
		logger.warning(f"[foreign_flow] {stock_code} 종가=0")
		return None

	total_won = aggregate_foreign_flow_5d(items, close)
	logger.info(f"[foreign_flow] {stock_code} 5d={total_won:+,}원 close={close:,.0f}")
	return total_won
