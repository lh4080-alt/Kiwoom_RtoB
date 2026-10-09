# -*- coding: utf-8 -*-
"""폭 지표 이력 재산출 — 정의 v2 (2026-10-09 Lee 지시).

기존 jsonl 'breadth'는 ① MDC 15:35 잠정 최종일 ② ETF·ETN 혼입 패널로 계산돼 왜곡됨.
정의 v2 (brief.build.breadth_frame): 유니버스 = KRX 상장 주권, P 이전은 MDC 확정 행, 최종일은
ka10066 KRX 확정. 결과는 jsonl 각 행에 'breadth_v2'로 추가하고 기존 'breadth'는 보존한다.
한계: 현재 상장 주권 기준 — 과거 상장폐지 종목 누락(생존편향).
"""
import asyncio
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    from modules.semi_trigger.kr_calendar import is_kr_trading_day, prev_kr_trading_day
    from brief.build import breadth_frame, ka10066_official, stock_universe
    from macro_monitor import load_history, upsert_daily_record
    from datetime import date
    import pandas as pd

    token = await get_semi_token()
    univ = stock_universe()
    off = await ka10066_official(token, univ)
    today = date.today()
    p = today if is_kr_trading_day(today) else pd.Timestamp(prev_kr_trading_day(today)).date()
    f = breadth_frame(p, off, univ)
    n = 0
    for r in load_history():
        d = pd.Timestamp(r['date']).date()
        if d not in f.index or pd.isna(f.loc[d, 'pct_above200']):
            continue
        row = f.loc[d]
        upsert_daily_record({'date': r['date'], 'breadth_v2': {
            'pct_above200': round(float(row['pct_above200']), 2),
            'ad_diff': int(row['ad_diff']), 'ad_cum': int(row['ad_cum']),
            'up': int(row['up']), 'dn': int(row['dn']), 'universe': int(row['universe']),
            'source': 'MDC 확정행 + ka10066(최종일)' if d == p else 'MDC 확정행'}})
        n += 1
    print(f'breadth_v2 기록 {n}행 | 유니버스 {int(f["universe"].iloc[-1])} | 최종일 {p}')
    print(f.tail(5)[['up', 'dn', 'ad_diff', 'ad_cum', 'pct_above200']].round(2).to_string())
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
