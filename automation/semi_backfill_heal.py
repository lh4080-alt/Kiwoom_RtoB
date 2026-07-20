# -*- coding: utf-8 -*-
"""월 1회 데이터 자가치유 백필 (2026-07-20 추가).

KR 휴장·US 개장인 날(제헌절/설/추석 등)에는 새벽 snapshot의 morning pipeline이
최신 US 데이터를 직전 KR 거래일 행에 덮어써서 그 행의 us 축이 하루 어긋난다.
이 스크립트가 최근 40영업일을 yfinance/키움 히스토리 원본값으로 재적재해서 원복한다.

스케줄: RtoB_Semi_MonthlyHeal — 매월 첫 토요일 09:00 (건강검진 10:00 이전).
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.semi_trigger.token_provider import get_semi_token
from modules.semi_trigger.backfill import backfill_factors
from utils.rate_limiter import requests


async def main() -> int:
    # semi 전용 -XMf61 토큰 (GDLLsq=Basic 매매봇 키 격리, 2026-07-21)
    token = await get_semi_token()
    if not token:
        print("[heal] 토큰 발급 실패")
        return 1
    res = await backfill_factors(end_dt=date.today().strftime("%Y%m%d"),
                                 token=token, days_target=40)
    print(f"[heal] 완료 saved_per_stock={res.get('saved_per_stock')}")
    await requests.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
