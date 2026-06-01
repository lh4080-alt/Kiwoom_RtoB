"""⑤ 메모리 가격 collector (DDR4/DDR5) — Phase 4 placeholder.

spec v3 §1: 가중 10%.
spec v3 §5: 일간 확보 어려움 (DRAMeXchange/TrendForce는 주간/월간) →
            완전 결측 시 ⑤ 가중 재분배 + 경고 허용.

현재 상태: 외부 수집 미구현. None 반환 → scoring.calc_semi_score가 자동 가중 재분배.
추후 DRAMeXchange/TrendForce 스크래핑 추가 가능 (별도 작업).

영구 원칙 #30: 봇 데몬 내부에서만 호출.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def carry_forward(history: list) -> Optional[float]:
	"""과거 일별 (date DESC) 리스트에서 가장 최근 non-None 값 반환.

	Args:
		history: [{date, memory_price}, ...] (date DESC)

	Returns: 가장 최근 가용한 memory_price, 없으면 None.
	"""
	if not history:
		return None
	for row in history:
		v = row.get('memory_price')
		if v is not None:
			return v
	return None


async def collect_memory_price() -> dict:
	"""메모리 가격 수집 (현재 미구현).

	Returns: {
	  'memory_price': None,    # 미구현 → 항상 None
	  'source': None,           # 추후 'dramexchange' 등으로 변경
	  'is_carry_forward': False,
	}

	scoring.calc_semi_score가 None 축을 자동으로 가중 재분배 (spec §8).
	"""
	logger.info("[memory_price] 미구현 — None 반환, 가중 재분배 폴백 사용")
	return {
		'memory_price': None,
		'source': None,
		'is_carry_forward': False,
	}
