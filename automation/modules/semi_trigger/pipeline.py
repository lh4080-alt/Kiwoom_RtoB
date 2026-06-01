"""semi_trigger 통합 pipeline — 5축 수집 + semi_score + JSON 출력 (shadow mode).

spec v3 §7 Phase 5:
  z 가중합 (0.40/0.20/0.20/0.10/0.10) → semi_score
  legacy_trigger (SOX+NVDA+MU 2/3) 병행
  daily_semi_trigger.json 산출

영구 원칙 #30: 봇 데몬 내부에서만 실행.
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

from . import db as st_db
from .etf_mapping import TARGET_UNDERLYINGS, UNDERLYING_NAMES
from .scoring import (
	WEIGHTS, BASELINE_MIN_DAYS,
	calc_zscore, calc_semi_score, calc_legacy_trigger,
)
from .collectors.us_memory import collect_us_memory
from .collectors.etf_flow import collect_etf_flows
from .collectors.fx import collect_fx_change
from .collectors.foreign_flow import collect_foreign_flow_5d
from .collectors.memory_price import collect_memory_price
from .collectors.nasdaq_futures import collect_nasdaq_futures

logger = logging.getLogger(__name__)

# shadow 모드 기본 임계값 (백테스트 확정 전 잠정)
DEFAULT_THRESHOLD = 1.0

# 출력 JSON 경로
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_JSON_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'daily_semi_trigger.json')


# 축 → DB 컬럼 매핑 (z-score baseline 추출용)
# Lee 6/2 수정: memory_price → nasdaq_futures 교체
AXIS_TO_DB_COL = {
	'us_memory':       'us_memory',
	'etf_flow':        'etf_flow',
	'fx':              'fx_change',
	'foreign_flow':    'foreign_flow_5d',
	'nasdaq_futures':  'nasdaq_futures',
}


def calc_axes_zscores(eval_date: str, stock_code: str, raw_factors: dict,
                      db_path: Optional[str] = None) -> dict:
	"""5축 raw 값 → z-score dict (DB baseline 사용).

	Args:
		eval_date: 오늘 (YYYY-MM-DD) — baseline에서 자기 자신 제외
		stock_code: 005930 / 000660
		raw_factors: {'us_memory', 'etf_flow', 'fx_change', 'foreign_flow_5d', 'memory_price'}

	Returns: ({z_values dict}, baseline_days)
	"""
	# 과거 21일 fetch (오늘 포함 가능) → 오늘 제외하면 최대 20일 baseline
	recent = st_db.fetch_recent_factors(stock_code, n=BASELINE_MIN_DAYS + 1, db_path=db_path)
	baseline_rows = [r for r in recent if r.get('date') != eval_date]
	baseline_days = len(baseline_rows)

	# raw_factors 키 매핑
	current_map = {
		'us_memory':       raw_factors.get('us_memory'),
		'etf_flow':        raw_factors.get('etf_flow'),
		'fx':              raw_factors.get('fx_change'),
		'foreign_flow':    raw_factors.get('foreign_flow_5d'),
		'nasdaq_futures':  raw_factors.get('nasdaq_futures'),
	}

	z_values = {}
	for axis, current_val in current_map.items():
		db_col = AXIS_TO_DB_COL[axis]
		baseline_vals = [r.get(db_col) for r in baseline_rows]
		z_values[axis] = calc_zscore(baseline_vals, current_val)
	return z_values, baseline_days


async def run_pipeline_evening(eval_date: str, token: str,
                                db_path: Optional[str] = None) -> dict:
	"""16:00 한국 마감 후 — etf_flow + foreign_flow_5d만 수집해서 DB 저장.

	08:30 morning에서 us_mem/fx/nq 보완 후 통합 score 산출 예정.

	Args:
		eval_date: YYYY-MM-DD
		token: 키움 API 토큰

	Returns: {
	  '005930': {'etf_flow', 'foreign_flow_5d'},
	  '000660': {'etf_flow', 'foreign_flow_5d'},
	}
	"""
	base_dt = eval_date.replace('-', '')
	logger.info(f"[pipeline_evening] start eval_date={eval_date}")

	etf_flows, ff_005930, ff_000660 = await asyncio.gather(
		collect_etf_flows(base_dt, token),
		collect_foreign_flow_5d('005930', base_dt, token),
		collect_foreign_flow_5d('000660', base_dt, token),
		return_exceptions=False,
	)

	out = {}
	for stock_code in TARGET_UNDERLYINGS:
		fflow = ff_005930 if stock_code == '005930' else ff_000660
		partial = {
			'etf_flow':        etf_flows.get(stock_code, {}).get('etf_flow'),
			'foreign_flow_5d': fflow,
		}
		st_db.upsert_factors(eval_date, stock_code, partial, db_path=db_path)
		out[stock_code] = partial
	logger.info(f"[pipeline_evening] done")
	return out


async def run_pipeline_morning(eval_date: str, token: str, mode: str = 'shadow',
                                threshold: float = DEFAULT_THRESHOLD,
                                json_path: Optional[str] = None,
                                db_path: Optional[str] = None) -> dict:
	"""08:30 시점 — us_memory + fx + nasdaq_futures 재계산 + 통합 score 산출.

	전제: evening pipeline이 같은 eval_date에 etf_flow + foreign_flow를 이미 저장.
	      (또는 같은 함수가 16:00에 직전 영업일 데이터 저장하고, morning이 그 직전 eval_date 사용)

	실제 운영:
	  T일 15:30 한국장 마감 후 16:00 evening — T일 데이터 저장
	  T+1일 08:30 morning — eval_date=T일 (직전 거래일) us_mem/fx/nq fetch + 통합

	Args:
		eval_date: YYYY-MM-DD (보통 직전 거래일)
		token: 키움 토큰
		mode/threshold/json_path/db_path: 동일

	Returns: 통합 output dict (JSON 구조)
	"""
	logger.info(f"[pipeline_morning] start eval_date={eval_date} mode={mode}")

	from api.external_index import fetch_change_pct, SYM_SOX, SYM_NVDA, SYM_MU

	# 미국 데이터 + NQ 재계산 + 보조 (SOX/NVDA/MU)
	(us_mem, fx_r, nq_r, sox, nvda, mu) = await asyncio.gather(
		collect_us_memory(),
		collect_fx_change(),
		collect_nasdaq_futures(),
		fetch_change_pct(SYM_SOX),
		fetch_change_pct(SYM_NVDA),
		fetch_change_pct(SYM_MU),
		return_exceptions=False,
	)

	targets = []
	for stock_code in TARGET_UNDERLYINGS:
		# DB에서 evening 저장된 부분 로드 — 최근 10개 중 eval_date 일치 row
		existing = st_db.fetch_recent_factors(stock_code, n=10, db_path=db_path)
		existing_today = next((r for r in existing if r.get('date') == eval_date), None) or {}

		raw_factors = {
			'us_memory':       us_mem.get('us_memory'),
			'fx_change':       fx_r.get('fx_change'),
			'nasdaq_futures':  nq_r.get('nasdaq_futures'),
			'etf_flow':        existing_today.get('etf_flow'),
			'foreign_flow_5d': existing_today.get('foreign_flow_5d'),
			'sox':             sox,
			'nvda':            nvda,
			'mu':              mu,
		}
		# DB 업데이트 (08:30 최신값으로 us_mem/fx/nq 덮어쓰기)
		st_db.upsert_factors(eval_date, stock_code, raw_factors, db_path=db_path)

		# z-score + semi_score + trigger
		z_values, baseline_days = calc_axes_zscores(
			eval_date, stock_code, raw_factors, db_path=db_path,
		)
		score_result = calc_semi_score(z_values)
		semi_score = score_result['semi_score']
		redistributed = score_result['weight_redistributed']

		baseline_ok = baseline_days >= BASELINE_MIN_DAYS
		trigger = 1 if (baseline_ok and semi_score is not None and
		                semi_score >= threshold) else 0
		legacy = calc_legacy_trigger(sox, nvda, mu)

		st_db.upsert_score(eval_date, stock_code, {
			'us_memory_z':          z_values.get('us_memory'),
			'etf_flow_z':           z_values.get('etf_flow'),
			'fx_z':                 z_values.get('fx'),
			'foreign_flow_z':       z_values.get('foreign_flow'),
			'memory_price_z':       z_values.get('nasdaq_futures'),  # 컬럼 재사용 (스키마 호환)
			'semi_score':           semi_score,
			'trigger':              trigger,
			'legacy_trigger':       legacy,
			'baseline_days':        baseline_days,
			'weight_redistributed': 1 if redistributed else 0,
		}, db_path=db_path)

		targets.append({
			'code':                  stock_code,
			'name':                  UNDERLYING_NAMES.get(stock_code, stock_code),
			'factors_raw':           raw_factors,
			'factors_z':             z_values,
			'semi_score':            semi_score,
			'trigger':               bool(trigger),
			'legacy_trigger':        bool(legacy),
			'baseline_days':         baseline_days,
			'weight_redistributed':  redistributed,
			'baseline_sufficient':   baseline_ok,
		})

	# 3) JSON 출력
	output = {
		'date':         eval_date,
		'mode':         mode,
		'generated_at': datetime.now().isoformat(timespec='seconds'),
		'params':       {'weights': WEIGHTS, 'threshold': threshold},
		'targets':      targets,
	}

	path = json_path or _JSON_PATH
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + '.tmp'
	with open(tmp, 'w', encoding='utf-8') as f:
		json.dump(output, f, ensure_ascii=False, indent=2)
	os.replace(tmp, path)

	logger.info(
		f"[pipeline_morning] done eval_date={eval_date} 005930.trigger={targets[0]['trigger']} "
		f"score={targets[0]['semi_score']} baseline={targets[0]['baseline_days']}"
	)
	return output


# 하위 호환 — 기존 run_pipeline 호출처는 morning 사용 (5축 한 번에 처리)
async def run_pipeline(eval_date: str, token: str, mode: str = 'shadow',
                       threshold: float = DEFAULT_THRESHOLD,
                       json_path: Optional[str] = None,
                       db_path: Optional[str] = None) -> dict:
	"""Deprecated alias — evening + morning을 한 번에 (백테스트/dry-run 용)."""
	await run_pipeline_evening(eval_date, token, db_path=db_path)
	return await run_pipeline_morning(eval_date, token, mode=mode,
	                                   threshold=threshold,
	                                   json_path=json_path, db_path=db_path)
