# -*- coding: utf-8 -*-
"""과거 행 국면 라벨 소급 — ka20006 코스피 일봉으로 compute_regime 재계산.

배경 (2026-09-17):
  - 국면 기능이 2026-09-08 도입이라 그 이전 jsonl 행엔 라벨이 없음 → "8일째" 카운터가
    도입일을 넘어 못 셈
  - 9/8~9/16 행은 yfinance(최신 봉 하루 밀림) 기준 — 소스가 ka20006과 섞여 있음
  - ka20006으로 전 행의 regime을 시점 정합(point-in-time: 그날까지 데이터만 사용)하게
    재계산해 upsert 병합 (null 아닌 regime dict가 기존값 통째로 교체)

실행: beelink에서 python tools/backfill_regime.py  (단발 — 스케줄 아님)
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'automation'))


async def main() -> int:
    import pandas as pd
    from modules.semi_trigger.token_provider import get_semi_token
    from macro_monitor import (compute_regime, fetch_kospi_index_closes,
                               load_history, upsert_daily_record)

    token = await get_semi_token()
    if not token:
        print('[regime_backfill] 토큰 발급 실패')
        return 1
    today = datetime.now().strftime('%Y%m%d')
    closes = await fetch_kospi_index_closes(token, today)
    if len(closes) < 270:
        print(f'[regime_backfill] 봉 부족: {len(closes)} < 270 — 재실행 불가')
        return 1

    # {YYYYMMDD: close} — jsonl 행(date=YYYYMMDD)과 짝짓기
    series = pd.Series({d.replace('-', ''): v for d, v in closes.items()}).sort_index()

    hist = load_history()
    n_full = n_skip = 0
    for r in hist:
        sub = series[series.index <= r['date']]
        if len(sub) < 270:
            n_skip += 1  # 지수 히스토리 부족 — 풀 계산 불가 행
            continue
        reg = compute_regime(dict(sub))
        if not reg:
            n_skip += 1
            continue
        upsert_daily_record({'date': r['date'], 'regime': reg})
        n_full += 1
    print(f'[regime_backfill] 완료: {n_full}행 갱신 / 스킵 {n_skip} / 전체 {len(hist)}행 '
          f'(ka20006 {len(closes)}봉, {min(series.index)}~{max(series.index)})')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
