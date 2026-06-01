"""semi_trigger snapshot — Lee 매수 판단용 4축 + 종목별 4신호 + semi_score 즉시 조회.

Lee 6/2 최종 수정:
- 점수 4축: us_memory 50% + legacy(SOX/NVDA) 30% + fx 10% + nq 10%
- 정보 표시: 종목별 4신호 (점수 X)
- 모든 z값 → 4일치 트렌드 (3일전/2일전/어제/오늘)

호출 3가지:
  1. 자동 02:00 KST
  2. 자동 05:30 KST
  3. 수동 텔레그램 score 명령
"""
import asyncio
import logging
import statistics
from datetime import datetime
from typing import Optional

from .pipeline import run_pipeline_morning, AXIS_TO_DB_COL
from .scoring import WEIGHTS, BASELINE_MIN_DAYS, calc_zscore

logger = logging.getLogger(__name__)

# z 트렌드 표시 일수 (3일전/2일전/어제/오늘)
Z_HISTORY_DAYS = 4


def calc_z_history(db_col: str, dates_desc: list, raw_history: list,
                   trail_n: int = Z_HISTORY_DAYS) -> list:
	"""최근 N일 각각의 z-score (walk-forward 일치).

	Args:
		db_col: 컬럼명 (디버그용)
		dates_desc: 일자 리스트 DESC ([0]=오늘, [1]=어제, ...)
		raw_history: 같은 순서 raw 값 리스트
		trail_n: 표시 일수 (기본 4)

	Returns: list of N z값 [3일전, 2일전, 어제, 오늘] (ASC 트렌드 순)
	"""
	if len(raw_history) < 2:
		return [None] * trail_n
	# raw_history DESC → 각 인덱스 i (0=오늘) 시점에서 baseline = raw_history[i+1 : i+1+20]
	z_list = []
	for offset in range(trail_n):
		if offset >= len(raw_history):
			z_list.append(None)
			continue
		cur = raw_history[offset]
		baseline = raw_history[offset + 1 : offset + 1 + BASELINE_MIN_DAYS]
		z = calc_zscore(baseline, cur)
		z_list.append(z)
	# DESC 순 (오늘이 [0]) → ASC 트렌드 (3일전 → 오늘)
	return list(reversed(z_list))


def fmt_z_history(z_list: list) -> str:
	"""z 4일치 리스트 → '/' 구분 문자열. 예: +0.50/+0.65/+0.80/+0.87"""
	parts = []
	for z in z_list:
		if z is None:
			parts.append('N/A')
		else:
			parts.append(f"{z:+.2f}")
	return '/'.join(parts)


def build_z_histories(stock_code: str, eval_date: str,
                      db_path: Optional[str] = None) -> dict:
	"""각 축에 대해 4일치 z history 계산. eval_date를 "오늘"로.

	Returns: {axis: [z_d-3, z_d-2, z_d-1, z_eval_date]}
	"""
	from . import db as st_db
	# 최근 30일 fetch (eval_date 포함 4일 + baseline 20일 + 여유)
	rows = st_db.fetch_recent_factors(stock_code, n=30, db_path=db_path)
	# DESC 정렬 후 eval_date 이전(포함)만 — eval_date 이후의 stale row 제외
	rows = sorted(rows, key=lambda r: r.get('date', ''), reverse=True)
	rows = [r for r in rows if r.get('date', '') <= eval_date]
	dates_desc = [r['date'] for r in rows]

	z_hist = {}
	for axis, db_col in AXIS_TO_DB_COL.items():
		raw_history = [r.get(db_col) for r in rows]
		z_hist[axis] = calc_z_history(db_col, dates_desc, raw_history)
	return z_hist


def fmt_pct(v):
	return 'N/A' if v is None else f"{v:+.3f}%"


def fmt_won(v):
	return 'N/A' if v is None else f"{v:>+,.0f}원"


def format_snapshot_message(output: dict, label: str,
                             z_histories: dict = None) -> str:
	"""snapshot output → 텔레그램 메시지 (Lee 6/2 최종 포맷).

	구조:
	  - 공통 4축 (최상위, 한 번만): us_memory, legacy(SOX·NVDA), fx, nasdaq_futures + semi_score
	  - 종목별 (005930, 000660): 종목별 4신호 + 외인 5일

	z_histories: {stock_code: {axis: [z_d-3, ..., z_d]}}  — 공통 4축은 005930 사용
	"""
	threshold = output.get('params', {}).get('threshold', 1.0)
	targets = output.get('targets', [])
	if not targets:
		return f"📊 [semi_trigger {output.get('date')} {label}] — targets 없음"

	# 공통 4축은 005930 기준 (글로벌 raw 동일)
	common = targets[0]
	fr = common.get('factors_raw', {})
	zh_common = (z_histories or {}).get(common['code'], {})

	def z_for(zh, axis):
		return fmt_z_history(zh.get(axis, [None] * Z_HISTORY_DAYS))

	# us_memory 하위 4종 (라이브 fetch)
	us_mem_sub = output.get('us_memory_sub', {})
	# SOX/NVDA raw + 평균
	sox = fr.get('sox')
	nvda = fr.get('nvda')
	legacy_avg = (sox + nvda) / 2.0 if (sox is not None and nvda is not None) else None

	# 점수 정보 (모든 종목 동일하지만 첫 번째 사용)
	semi_score = common.get('semi_score')
	score_str = f"{semi_score:+.3f}" if semi_score is not None else "N/A"
	trig = "🎯 TRIGGER" if common.get('trigger') else "⏸️ 미달"
	redistr = " (가중재분배)" if common.get('weight_redistributed') else ""

	lines = [
		f"📊 [semi_trigger {output.get('date')} {label}]",
		f"({output.get('generated_at', '')})",
		"",
		f"점수 가중: us_mem 50% / legacy(SOX·NVDA) 30% / fx 10% / nq 10%",
		"",
		"━━ 공통 4축 (점수 산출) ━━",
		"",
		f"① us_memory (50%)  {fmt_pct(fr.get('us_memory'))}  z={z_for(zh_common, 'us_memory')}",
	]
	if us_mem_sub:
		for sym in ('MU', 'WDC', 'SNDK', 'STX'):
			v = us_mem_sub.get(sym)
			lines.append(f"   ─ {sym:<4s} {fmt_pct(v)}")
	lines.extend([
		"",
		f"② legacy(SOX·NVDA) (30%)  {fmt_pct(legacy_avg)}  z={z_for(zh_common, 'legacy_sox_nvda')}",
		f"   ─ SOX  {fmt_pct(sox)}",
		f"   ─ NVDA {fmt_pct(nvda)}",
		"",
		f"③ fx_change (10%)  {fmt_pct(fr.get('fx_change'))}  z={z_for(zh_common, 'fx')}",
		f"④ nasdaq_futures (10%)  {fmt_pct(fr.get('nasdaq_futures'))}  z={z_for(zh_common, 'nasdaq_futures')}",
		"",
		f"semi_score: {score_str}{redistr}  {trig} (≥{threshold})",
	])

	# 종목별 섹션 (종목별 4신호 + 외인 5일)
	for t in targets:
		code = t['code']
		fr_t = t.get('factors_raw', {})
		zh = (z_histories or {}).get(code, {})
		base = t.get('baseline_days', 0)
		base_ok = t.get('baseline_sufficient')
		base_str = f"{base}일 ✅" if base_ok else f"{base}일 ⚠️부족"

		lines.extend([
			"",
			f"━━ [{code}] {t['name']} (baseline {base_str}) ━━",
			f"  주가 등락률      {fmt_pct(fr_t.get('price_change'))}  z={z_for(zh, 'price_change')}",
			f"  거래대금         {fmt_won(fr_t.get('volume_amount'))}  z={z_for(zh, 'volume_amount')}",
			f"  거래량 변화율    {fmt_pct(fr_t.get('volume_ratio'))}  z={z_for(zh, 'volume_ratio')}",
			f"  프로그램 순매수  {fmt_won(fr_t.get('program_net'))}  z={z_for(zh, 'program_net')}",
			f"  외인 5일 누적    {fmt_won(fr_t.get('foreign_flow_5d'))}  z={z_for(zh, 'foreign_flow')}",
		])

	lines.append("\n📌 z 트렌드: 3일전/2일전/어제/오늘")
	return "\n".join(lines)


async def take_snapshot(token: str, eval_date: str, label: str = 'manual',
                        db_path: Optional[str] = None,
                        json_path: Optional[str] = None,
                        send_telegram: bool = True) -> dict:
	"""snapshot — DB write + 텔레그램 전송 + z 4일치 history 포함."""
	logger.info(f"[snapshot] label={label} eval_date={eval_date}")
	output = await run_pipeline_morning(
		eval_date=eval_date,
		token=token,
		mode='snapshot',
		threshold=1.0,
		db_path=db_path,
		json_path=json_path,
	)

	# z 4일치 history per stock
	from .etf_mapping import TARGET_UNDERLYINGS
	z_histories = {}
	for stock_code in TARGET_UNDERLYINGS:
		z_histories[stock_code] = build_z_histories(stock_code, eval_date, db_path=db_path)

	# us_memory 하위 4종 (yfinance 라이브 — collect_us_memory 결과 활용)
	# pipeline output엔 없으므로 별도 fetch
	try:
		from .collectors.us_memory import collect_us_memory
		us_mem_result = await collect_us_memory()
		output['us_memory_sub'] = us_mem_result.get('symbols', {})
	except Exception:
		logger.exception("[snapshot] us_memory 하위 fetch 실패")
		output['us_memory_sub'] = {}

	if send_telegram:
		from telegram.tel_send import tel_send
		msg = format_snapshot_message(output, label, z_histories=z_histories)
		try:
			await tel_send(msg)
		except Exception:
			logger.exception("[snapshot] 텔레그램 전송 실패")
	return output


def resolve_eval_date(db_path: Optional[str] = None) -> Optional[str]:
	"""가장 최근 정상 evening 완료 일자 자동 추출.

	조건: us_memory + volume_amount 둘 다 있음 (evening + morning 완료).
	morning만 호출된 row (us_mem 있고 종목별 4신호 None)는 제외.
	"""
	from . import db as st_db
	from .etf_mapping import TARGET_UNDERLYINGS
	rows = st_db.fetch_recent_factors(TARGET_UNDERLYINGS[0], n=20, db_path=db_path)
	for r in rows:
		if r.get('us_memory') is not None and r.get('volume_amount') is not None:
			return r.get('date')
	# 폴백 — 정상 일자 없으면 가장 최근
	return rows[0].get('date') if rows else None
