"""semi_trigger z-score 계산 + semi_score 가중합.

spec v3 §1:
  semi_score = 0.40·us_memory_z + 0.20·etf_flow_z + 0.20·fx_z
             + 0.10·foreign_flow_z + 0.10·memory_price_z

spec v3 §8:
  - baseline < 20일 → z NULL, trigger 보류
  - 축 결측 시 가중 재분배 + 경고
"""
import logging
import statistics
from typing import Optional

logger = logging.getLogger(__name__)

# 가중치 (Lee 6/2 최종 수정: 4축 — us_mem 50% + legacy 30% + fx 10% + nq 10%)
WEIGHTS = {
	'us_memory':        0.50,  # MU/WDC/SNDK/STX 평균
	'legacy_sox_nvda':  0.30,  # SOX + NVDA 평균 (신규 축)
	'fx':               0.10,
	'nasdaq_futures':   0.10,
}
# 종목별 4신호 (price_change/volume_amount/volume_ratio/program_net)와
# foreign_flow는 점수 기여 X, 정보 표시만.

# baseline 최소 요구일수
BASELINE_MIN_DAYS = 20


def calc_zscore(baseline_values: list, current_value: float) -> Optional[float]:
	"""baseline 표본 + 현재값 → z-score.

	Args:
		baseline_values: 과거 N일 raw 값 (None 제외 필요)
		current_value: 오늘 raw 값

	Returns:
		z = (current - mean) / std. baseline < 2 또는 std=0이면 None.
	"""
	# None 제외
	clean = [v for v in baseline_values if v is not None]
	if len(clean) < 2 or current_value is None:
		return None
	try:
		mu = statistics.mean(clean)
		sigma = statistics.stdev(clean)
	except statistics.StatisticsError:
		return None
	if sigma == 0:
		return None
	return (current_value - mu) / sigma


def calc_semi_score(z_values: dict) -> dict:
	"""5축 z-score 가중합 → semi_score.

	축 결측(None) 시 그 축 가중을 나머지 유효 축에 비례 재분배.
	모든 축이 None이면 None 반환.

	Args:
		z_values: {'us_memory': z, 'etf_flow': z, 'fx': z,
		            'foreign_flow': z, 'memory_price': z}
		            각 값은 float 또는 None.

	Returns: {
	  'semi_score': float | None,
	  'weight_redistributed': bool,  # 결측 축이 있어 재분배됐는지
	  'used_axes': list of str,       # 가중합에 포함된 축
	}
	"""
	# 유효 축 선별
	valid_axes = {k: v for k, v in z_values.items() if v is not None}
	if not valid_axes:
		return {
			'semi_score': None,
			'weight_redistributed': False,
			'used_axes': [],
		}

	# 가중치 정상화 — 유효 축의 가중치 합으로 나눔
	total_weight = sum(WEIGHTS.get(k, 0.0) for k in valid_axes)
	if total_weight == 0:
		return {'semi_score': None, 'weight_redistributed': False, 'used_axes': []}

	score = sum(
		(WEIGHTS.get(k, 0.0) / total_weight) * v
		for k, v in valid_axes.items()
	)
	redistributed = len(valid_axes) < len(WEIGHTS)

	if redistributed:
		missing = [k for k in WEIGHTS if k not in valid_axes]
		logger.warning(
			f"[semi_score] 가중 재분배: 결측 {missing}, "
			f"유효 {list(valid_axes.keys())}, score={score:.3f}"
		)

	return {
		'semi_score': score,
		'weight_redistributed': redistributed,
		'used_axes': list(valid_axes.keys()),
	}


def is_baseline_sufficient(baseline_days: int) -> bool:
	"""baseline 충분 여부 (>= 20일)."""
	return baseline_days >= BASELINE_MIN_DAYS


def calc_legacy_trigger(sox, nvda, mu,
                       threshold: float = 0.3, min_count: int = 2) -> int:
	"""기존 stick 룰 (SOX/NVDA/MU 2/3 이상 +0.3%↑) 재현 — shadow 병행 비교용.

	None은 "상승 아님"으로 카운트 (보수적).

	Returns: 1 (trigger) / 0.
	"""
	count = 0
	for v in (sox, nvda, mu):
		if v is not None and v >= threshold:
			count += 1
	return 1 if count >= min_count else 0
