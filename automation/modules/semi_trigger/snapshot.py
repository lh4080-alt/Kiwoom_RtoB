"""semi_trigger snapshot — Lee 수동 판단용 5축 + semi_score 즉시 조회.

호출 3가지:
  1. 자동 02:00 KST (미국 정규장 중반)
  2. 자동 05:30 KST (미국 ET 16:30, 정규장 마감 30분 후)
  3. 수동 텔레그램 `score` 명령

모두 동일 함수 사용 — DB write 포함 (Lee 요구사항: "데이터는 모두 저장").
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from .pipeline import run_pipeline_morning
from .scoring import WEIGHTS, BASELINE_MIN_DAYS

logger = logging.getLogger(__name__)


def format_snapshot_message(output: dict, label: str) -> str:
	"""snapshot output → 텔레그램 메시지 (Lee 표 포맷)."""
	weights = output.get('params', {}).get('weights', WEIGHTS)
	threshold = output.get('params', {}).get('threshold', 1.0)

	lines = [
		f"📊 [semi_trigger {output.get('date')} {label}]",
		f"({output.get('generated_at', '')})",
		"",
		f"가중: us_mem 40% / 종목4신호 각 5%(합20%) / fx 20% / foreign 10% / nq 10%",
	]

	for t in output.get('targets', []):
		fr = t.get('factors_raw', {})
		fz = t.get('factors_z', {})

		def fmt_pct(v):
			return 'N/A' if v is None else f"{v:+.3f}%"

		def fmt_won(v):
			return 'N/A' if v is None else f"{v:>+15,.0f}원"

		def fmt_z(v):
			return f"z={v:+.2f}" if v is not None else "z=N/A"

		semi_score = t.get('semi_score')
		score_str = f"{semi_score:+.3f}" if semi_score is not None else "N/A"
		trig = "🎯 TRIGGER" if t.get('trigger') else "⏸️ 미달"
		base = t.get('baseline_days', 0)
		base_ok = t.get('baseline_sufficient')
		base_str = f"{base}일 ✅" if base_ok else f"{base}일 ⚠️부족"
		legacy = "🟢 ON" if t.get('legacy_trigger') else "⚪ OFF"
		redistr = " (가중재분배)" if t.get('weight_redistributed') else ""

		lines.extend([
			"",
			f"━━ [{t['code']}] {t['name']} (baseline {base_str}) ━━",
			f"  ① us_memory      {fmt_pct(fr.get('us_memory'))}  {fmt_z(fz.get('us_memory'))}",
			f"  ──── 종목 4신호 (각 5%) ────",
			f"  주가 등락률      {fmt_pct(fr.get('price_change'))}  {fmt_z(fz.get('price_change'))}",
			f"  거래대금         {fmt_won(fr.get('volume_amount'))}  {fmt_z(fz.get('volume_amount'))}",
			f"  거래량 변화율    {fmt_pct(fr.get('volume_ratio'))}  {fmt_z(fz.get('volume_ratio'))}",
			f"  프로그램 순매수  {fmt_won(fr.get('program_net'))}  {fmt_z(fz.get('program_net'))}",
			f"  ────",
			f"  ② fx_change      {fmt_pct(fr.get('fx_change'))}  {fmt_z(fz.get('fx'))}",
			f"  ③ foreign_5d     {fmt_won(fr.get('foreign_flow_5d'))}  {fmt_z(fz.get('foreign_flow'))}",
			f"  ④ nasdaq_futures {fmt_pct(fr.get('nasdaq_futures'))}  {fmt_z(fz.get('nasdaq_futures'))}",
			f"  semi_score: {score_str}{redistr}  {trig} (≥{threshold})",
			f"  legacy(SOX/NVDA/MU 2/3): {legacy}",
		])

	return "\n".join(lines)


async def take_snapshot(token: str, eval_date: str, label: str = 'manual',
                        db_path: Optional[str] = None,
                        json_path: Optional[str] = None,
                        send_telegram: bool = True) -> dict:
	"""5축 snapshot — DB write + 텔레그램 전송 (Lee 매수 판단용).

	Args:
		token: 키움 토큰
		eval_date: YYYY-MM-DD (evening 저장된 가장 최근 일자)
		label: '02:00' / '05:30' / 'manual' 등 알림 헤더 라벨
		db_path: DB 경로 override
		json_path: JSON 출력 경로 override
		send_telegram: False 시 출력만 (테스트용)

	Returns: morning pipeline output dict
	"""
	logger.info(f"[snapshot] label={label} eval_date={eval_date}")
	output = await run_pipeline_morning(
		eval_date=eval_date,
		token=token,
		mode='snapshot',
		threshold=1.0,
		db_path=db_path,
		json_path=json_path,
	)

	if send_telegram:
		from telegram.tel_send import tel_send
		msg = format_snapshot_message(output, label)
		try:
			await tel_send(msg)
		except Exception:
			logger.exception("[snapshot] 텔레그램 전송 실패")
	return output


def resolve_eval_date(db_path: Optional[str] = None) -> Optional[str]:
	"""가장 최근 정상 evening 저장 일자 자동 추출.

	etf_flow가 None 또는 0 이하인 row는 비정상 (한국장 진행 중 부분 저장 등) → 제외.
	가장 최근 etf_flow > 0인 일자 반환.

	Returns: YYYY-MM-DD or None.
	"""
	from . import db as st_db
	from .etf_mapping import TARGET_UNDERLYINGS
	rows = st_db.fetch_recent_factors(TARGET_UNDERLYINGS[0], n=20, db_path=db_path)
	for r in rows:
		ef = r.get('etf_flow')
		if ef is not None and ef > 0:
			return r.get('date')
	# 폴백 — 정상 evening 일자 없으면 가장 최근
	return rows[0].get('date') if rows else None
