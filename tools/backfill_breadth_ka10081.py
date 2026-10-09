# -*- coding: utf-8 -*-
"""폭 정의 v2 장기 패널 — ka10081 수정주가로 KRX 상장 주권 일봉 백필 (2014-07~, 2026-10-09 Lee 지시).

MDC 패널(2021~·ETF 혼입·최종일 잠정)과 독립된 자체 패널을 만든다. ka10081은 V1에서 pykrx와
60일 0원 일치. 종목당 600봉씩 base_dt를 과거로 이동하며 2014-07-01 이전까지 수집.
출력: config/data/breadth_k81_panel.parquet (index=날짜, columns=종목코드, 값=수정 종가)
재개: 실행 중 200종목마다 저장, 재실행 시 이미 있는 종목은 건너뜀.
한계: 현재 상장 주권 기준 — 과거 상장폐지 종목 누락(생존편향).
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
OUT = os.path.join(ROOT, 'config', 'data', 'breadth_k81_panel.parquet')
START = '20140701'


async def fetch_code(code, token, fn) -> dict:
    out, base = {}, datetime.now().strftime('%Y%m%d')
    for _ in range(8):
        for attempt in range(3):
            r = await fn(code, base_dt=base, token=token, silent=True)
            if r.get('return_code') == 0:
                break
            await asyncio.sleep(2 * (attempt + 1))
        rows = [c for c in r.get('candles', []) if c.get('close')]
        new = {str(c['date']): float(c['close']) for c in rows if str(c['date']) not in out}
        if not new:
            break
        out.update(new)
        oldest = min(new)
        if oldest <= START:
            break
        base = (datetime.strptime(oldest, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    return {d: v for d, v in out.items() if d >= START}


def save(cols: dict):
    df = pd.DataFrame({c: pd.Series(v) for c, v in cols.items()})
    df.index = pd.to_datetime(df.index)
    df.sort_index().to_parquet(OUT)


async def refresh(token, fn, cols: dict) -> dict:
    """주간 갱신 — 기존 종목은 최근 600봉 재조회. 겹치는 구간의 비율이 1이 아니면(수정주가 변경)
    전체 이력을 다시 받는다. 신규 상장 주권은 전체 수집."""
    from brief.build import stock_universe
    univ = sorted(stock_universe())
    stats = {'refetch': 0, 'new': 0, 'ok': 0}
    for i, code in enumerate(univ):
        if code not in cols:
            cols[code] = await fetch_code(code, token, fn)
            stats['new'] += 1
            continue
        r = await fn(code, base_dt=datetime.now().strftime('%Y%m%d'), token=token, silent=True)
        recent = {str(c['date']): float(c['close']) for c in r.get('candles', []) if c.get('close')}
        common = [d for d in recent if d in cols[code]]
        if common and any(abs(recent[d] - cols[code][d]) > 0 for d in common):
            cols[code] = await fetch_code(code, token, fn)
            stats['refetch'] += 1
        else:
            cols[code].update(recent)
            stats['ok'] += 1
        if (i + 1) % 500 == 0:
            print(f'[k81 refresh] {i + 1}/{len(univ)} {stats}', flush=True)
    print(f'[k81 refresh] 완료 {stats}', flush=True)
    return cols


async def main() -> int:
    from api.daily_candle import fn_ka10081
    from brief.build import stock_universe
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    codes = sorted(stock_universe())
    cols = {}
    if os.path.exists(OUT):
        old = pd.read_parquet(OUT)
        cols = {c: {d.strftime('%Y%m%d'): v for d, v in old[c].dropna().items()} for c in old.columns}
        print(f'[k81] 재개 — 기존 {len(cols)}종목')
    if '--refresh' in sys.argv:
        save(await refresh(token, fn_ka10081, cols))
        return 0
    todo = [c for c in codes if c not in cols]
    print(f'[k81] 대상 {len(codes)} | 남은 {len(todo)}', flush=True)
    fails = []
    for i, code in enumerate(todo):
        try:
            cols[code] = await fetch_code(code, token, fn_ka10081)
            if not cols[code]:
                fails.append(code)
        except Exception as e:
            fails.append(code)
            if len(fails) <= 5:
                print(f'  {code} 실패: {e}', flush=True)
        if (i + 1) % 200 == 0:
            save(cols)
            if (i + 1) % 1000 == 0:
                token = await get_semi_token()   # 장시간 실행 — 토큰 갱신
            print(f'[k81] 진행 {i + 1}/{len(todo)} (실패 {len(fails)})', flush=True)
    save(cols)
    df = pd.read_parquet(OUT)
    print(f'[k81] 완료 {df.shape} {df.index.min().date()}~{df.index.max().date()} | 실패 {len(fails)}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
