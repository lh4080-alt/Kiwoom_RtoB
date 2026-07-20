# -*- coding: utf-8 -*-
"""semi_trigger 단독 운영 진입점 — RtoB 슬림화 (2026-07-16 Lee 지시).

RtoB 매매 데몬은 잠정중단 상태. Windows 작업스케줄러가 이 스크립트를
원샷 호출해서 semi_trigger 기능만 유지한다 (주문 없음, 시세 조회 전용).

modes:
  evening              16:00 KST 거래일 — 한국 데이터 수집·저장 (ka10081/ka90013/ka10059)
  snapshot <slot>      미국 마감 후 — 통합 semi_score 산출 + 텔레그램 발송

미국 마감 스냅샷은 서머타임 자동 판단 (Lee 7/16 지시):
  미국 정규장 마감 = 서머타임(EDT) KST 05:00 / 윈터타임(EST) KST 06:00.
  작업스케줄러에 05:30·06:30 두 슬롯을 모두 등록해 두고, 실행 시점에
  DST 여부를 계산해서 맞는 슬롯만 발송하고 나머지는 조용히 종료한다.
  (02:00 장중 중간 점검 슬롯은 Lee 7/16 지시로 제거)

스케줄 (작업스케줄러):
  RtoB_Semi_Evening1600   평일 16:00  python semi_once.py evening
  RtoB_Semi_Snapshot0530  매일 05:30  python semi_once.py snapshot 0530  (서머타임에만 발송)
  RtoB_Semi_Snapshot0630  매일 06:30  python semi_once.py snapshot 0630  (윈터타임에만 발송)

주: 기존 '영구 원칙 #30(키움 호출은 데몬 내부에서만)'은 데몬 상주 시절의
rate-limit 일원화 원칙. 데몬 잠정중단 후에는 이 원샷 스크립트가 유일한
키움 호출처이므로 충돌 없음.
"""
import asyncio
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _us_dst_active_now() -> bool:
	"""미국 동부 서머타임 여부 — 3월 둘째 일요일 ~ 11월 첫째 일요일 (ET 날짜 기준).

	tzdata 의존 없이 규칙으로 계산. 경계일 02:00 ET 세부는 무시해도 됨 —
	스냅샷 시각(KST 새벽)은 ET 기준 전일 오후라 전환 시각과 겹치지 않고,
	전환 당일은 주말이라 거래도 없다.
	"""
	from datetime import timedelta, timezone
	now_et_std = datetime.now(timezone.utc) - timedelta(hours=5)  # EST(UTC-5) 기준 날짜
	y = now_et_std.year
	d = date(y, 3, 8)
	dst_start = d + timedelta(days=(6 - d.weekday()) % 7)   # 3월 둘째 일요일
	d = date(y, 11, 1)
	dst_end = d + timedelta(days=(6 - d.weekday()) % 7)     # 11월 첫째 일요일
	return dst_start <= now_et_std.date() < dst_end


def _active_usclose_slot() -> str:
	"""지금 계절에 발송해야 하는 슬롯: 서머타임 0530 / 윈터타임 0630."""
	return "0530" if _us_dst_active_now() else "0630"


async def _kr_market_traded_today(token: str, today: date) -> bool:
	"""오늘이 실제 거래일이었는지 — 키움 일봉의 최신 캔들 날짜로 판정.

	holidays 라이브러리는 쓰지 않음: 2026-07-17 제헌절을 휴일로 오판해서
	개장일 수집을 스킵한 사고 있음 (제헌절은 2008년부터 공휴일·휴장일 아님).
	거래소 데이터 자체가 진실 — 오늘 캔들이 있으면 거래일.
	"""
	from api.daily_candle import fn_ka10081
	base_dt = today.strftime('%Y%m%d')
	resp = await fn_ka10081('005930', base_dt=base_dt, token=token, silent=True)
	if not isinstance(resp, dict) or resp.get('return_code') != 0:
		raise RuntimeError(f"ka10081 실패: {resp.get('return_msg') if isinstance(resp, dict) else resp}")
	candles = resp.get('candles', [])
	latest = max((str(c.get('date', '')) for c in candles), default='')
	return latest == base_dt


async def run_evening() -> int:
	today = date.today()
	if today.weekday() >= 5:
		print(f"[semi_once] 주말 {today} — evening 스킵")
		return 0
	# 토큰: semi 전용 -XMf61 (search 키). GDLLsq(fn_au10001)는 Kiwoom_Basic 매매봇
	# 키라 여기서 발급하면 Basic의 REST 토큰이 무효화됨 (7/20 사고 — 2026-07-21 분리)
	from modules.semi_trigger.token_provider import get_semi_token
	from modules.semi_trigger.pipeline import run_pipeline_evening
	token = await get_semi_token()
	if not token:
		from telegram.tel_send import tel_send
		await tel_send("⚠️ [semi_once evening] 키움 토큰 발급 실패 — 수집 스킵")
		return 1
	if not await _kr_market_traded_today(token, today):
		print(f"[semi_once] 휴장일 {today} (오늘 캔들 없음) — evening 스킵")
		return 0
	await run_pipeline_evening(eval_date=today.isoformat(), token=token)
	print(f"[semi_once] evening 완료 {datetime.now().isoformat(timespec='seconds')}")
	return 0


async def run_snapshot(label: str) -> int:
	# 토큰: semi 전용 -XMf61 (GDLLsq 격리 — run_evening 주석 참고)
	from modules.semi_trigger.token_provider import get_semi_token
	from modules.semi_trigger.snapshot import take_snapshot, resolve_eval_date
	from telegram.tel_send import tel_send
	token = await get_semi_token()
	if not token:
		await tel_send(f"⚠️ [snapshot {label}] 키움 토큰 발급 실패")
		return 1
	eval_date = resolve_eval_date()
	if not eval_date:
		await tel_send(
			f"⚠️ [snapshot {label}] daily_factors 비어있음 — "
			"직전 16:00 evening pipeline 확인 필요"
		)
		return 1
	await take_snapshot(token=token, eval_date=eval_date, label=label,
	                    send_telegram=True)
	print(f"[semi_once] snapshot({label}) 완료 eval_date={eval_date}")
	return 0


async def _main_async(mode: str, label: str) -> int:
	try:
		if mode == "evening":
			return await run_evening()
		return await run_snapshot(label)
	finally:
		try:
			from utils.rate_limiter import requests
			await requests.close()
		except Exception:
			pass


# 작업스케줄러 인자는 ASCII 키만 사용 (cmd 인코딩 문제 회피) → 여기서 라벨 매핑
_LABEL_MAP = {
	"0530": "05:30 KST (미국 마감 후)",
	"0630": "06:30 KST (미국 마감 후·윈터타임)",
}
_USCLOSE_SLOTS = ("0530", "0630")


def main() -> int:
	mode = sys.argv[1] if len(sys.argv) > 1 else ""
	if mode not in ("evening", "snapshot"):
		print('사용법: python semi_once.py [evening | snapshot <0530|0630|라벨>]')
		return 2
	label = sys.argv[2] if len(sys.argv) > 2 else "manual"
	# 미국 마감 슬롯이면 서머타임 여부로 발송 슬롯 게이트
	if mode == "snapshot" and label in _USCLOSE_SLOTS:
		active = _active_usclose_slot()
		if label != active:
			print(f"[semi_once] slot {label} 스킵 — 현재 계절의 발송 슬롯은 {active} "
			      f"(US DST={'ON' if _us_dst_active_now() else 'OFF'})")
			return 0
	label = _LABEL_MAP.get(label, label)
	return asyncio.run(_main_async(mode, label))


if __name__ == "__main__":
	sys.exit(main())
