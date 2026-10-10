# -*- coding: utf-8 -*-
"""semi_trigger.db 무효 플래그 (2026-10-10 Lee 지시 — 기존 행 수정·삭제 없음).

daily_factors.price_change(삼전·하닉)를 ka10081 확정 종가 재계산과 대조해, 어긋난 (date, code)를
별도 테이블 invalid_flags에 기록한다. 쓰기 전 DB 전체를 sqlite backup API로 백업.
레거시 semi_trigger는 동결 — price_change는 점수 미사용(표시 전용)이고 사후 수익률 계산 경로에서 읽지 않음.
"""
import asyncio
import os
import sqlite3
import sys
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
DB = os.path.join(BASE, '..', 'config', 'data', 'semi_trigger.db')
TOL = 0.05   # %p — macro jsonl 스캔과 동일


async def main() -> int:
    from api.daily_candle import fn_ka10081
    from modules.semi_trigger.token_provider import get_semi_token
    t = await get_semi_token()
    ref = {}
    for c in ('005930', '000660'):
        r = await fn_ka10081(c, base_dt=datetime.now().strftime('%Y%m%d'), token=t, silent=True)
        s = pd.Series({str(x['date']): float(x['close']) for x in r['candles']}).sort_index()
        ref[c] = (s.pct_change() * 100).to_dict()
    con = sqlite3.connect(DB)
    bak = DB + f'.bak_flag_{datetime.now():%Y%m%d_%H%M%S}'
    with sqlite3.connect(bak) as b:
        con.backup(b)
    rows = con.execute("select date, stock_code, price_change from daily_factors "
                       "where stock_code in ('005930','000660') and price_change is not null").fetchall()
    con.execute('create table if not exists invalid_flags (date text, stock_code text, field text, '
                'reason text, flagged_at text, primary key (date, stock_code, field))')
    n = 0
    for d, c, v in rows:
        k = d.replace('-', '')
        x = ref[c].get(k)
        if x is not None and not pd.isna(x) and abs(v - x) > TOL:
            con.execute('insert or replace into invalid_flags values (?,?,?,?,?)',
                        (d, c, 'price_change', f'ka10081 확정 종가 재계산 {x:+.2f} vs 기록 {v:+.2f} — 16:00 수집 미확정 종가',
                         datetime.now().isoformat(timespec='seconds')))
            n += 1
            print(d, c, f'기록 {v:+.2f} 확정 {x:+.2f}')
    con.commit()
    print(f'대조 {len(rows)}행 | 플래그 {n} | 백업 {os.path.basename(bak)}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
