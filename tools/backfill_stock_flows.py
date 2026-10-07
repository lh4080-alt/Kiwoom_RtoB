# -*- coding: utf-8 -*-
"""§10-2 삼성전자·SK하이닉스 종목별 투자자 수급 백필 — ka10059 (2010-01-04~).

프로브 확인(2026-10-07): ka10059 dt 과거 조회 가능, 2010-01-04부터.
단위: amt_qty_tp='1'(금액) — 백만원 추정, 첫 응답에서 실측 검증 후 확정.
저장: config/data/stock_flows.parquet (index=date, cols: {code}_frgnr_eok 등)
룩어헤드 방지: 오늘(장중)은 저장하지 않음 — 확정치만 (지시서 §5).

실행: beelink에서 python tools/backfill_stock_flows.py [시작일 YYYYMMDD]
"""
import asyncio
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
OHLC_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
OUT = os.path.join(BASE, '..', 'config', 'data', 'stock_flows.parquet')
STOCKS = ('005930', '000660')
FIELDS = {'frgnr_invsr': 'frgnr', 'orgn': 'orgn', 'ind_invsr': 'ind'}


def _num(s):
    s = str(s or '0').replace('+', '')
    if s.startswith('--'):
        s = '-' + s[2:]
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


async def fetch_day(token, code, d):
    """ka10059 당일 행 — {필드: 억원}. 행 없으면 None."""
    from utils.rate_limiter import requests
    import utils.config as config
    r = await requests.post(
        config.get_host_url() + '/api/dostk/stkinfo',
        headers={'Content-Type': 'application/json;charset=UTF-8',
                 'authorization': f'Bearer {token}', 'cont-yn': 'N',
                 'next-key': '', 'api-id': 'ka10059'},
        json={'dt': d, 'stk_cd': code, 'amt_qty_tp': '1', 'trde_tp': '0', 'unit_tp': '1'})
    for it in (r.json() or {}).get('stk_invsr_orgn') or []:
        if str(it.get('dt', '')).replace('-', '') == d:
            # 백만원 → 억원 (단위 실측 후 필요시 조정 — 아래 main의 자릿수 점검 참고)
            return {f'{code}_{out}': round(_num(it.get(src)) / 100, 1)
                    for src, out in FIELDS.items()}
    return None


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    if not token:
        print('[stock_flows] 토큰 발급 실패')
        return 1

    start = sys.argv[1] if len(sys.argv) > 1 else '20100104'
    ohlc = pd.read_parquet(OHLC_PATH)
    # 확정치만: 오늘 제외 (장 마감 후 갱신분만 저장 — 지시서 §5)
    today = datetime.now().strftime('%Y%m%d')
    days = [d.strftime('%Y%m%d') for d in ohlc.index
            if d.strftime('%Y%m%d') >= start and d.strftime('%Y%m%d') < today]
    print(f'[stock_flows] 대상 {len(days)}일 ({days[0]}~{days[-1]}) × {len(STOCKS)}종목')

    # 단위 실측 — 최근 날짜 하나로 자릿수 확인
    probe = await fetch_day(token, '005930', days[-5])
    if probe:
        print(f'[stock_flows] 단위 점검 {days[-5]}: {probe}')
        print('[stock_flows] → 백만원 기준 ±수천억(=raw ±수만)이면 정상. 이상하면 중단 후 확인')

    done = {}
    if os.path.exists(OUT):
        old = pd.read_parquet(OUT)
        done = {d.strftime('%Y%m%d'): row for d, row in old.iterrows()}
        print(f'[stock_flows] 기존 {len(done)}일 이어받기')

    rows, miss = {}, []
    for i, d in enumerate(days):
        if d in done:
            rows[d] = dict(done[d])
            continue
        rec = {}
        for code in STOCKS:
            try:
                got = await fetch_day(token, code, d)
                if got:
                    rec.update(got)
            except Exception as e:
                miss.append(f'{d}/{code}')
                if len(miss) <= 3:
                    print(f'[stock_flows] {d}/{code} 실패: {e}')
            await asyncio.sleep(0.05)
        if rec:
            rows[d] = rec
        if (i + 1) % 200 == 0:
            print(f'[stock_flows] 진행 {i + 1}/{len(days)} (결측 {len(miss)})', flush=True)
            df_tmp = pd.DataFrame.from_dict(rows, orient='index').sort_index()
            df_tmp.index.name = 'date'
            df_tmp.to_parquet(OUT)  # 중간 저장
    df = pd.DataFrame.from_dict(rows, orient='index').sort_index()
    df.index.name = 'date'
    df.to_parquet(OUT)
    print(f'[stock_flows] 저장 {len(df)}일 → {OUT} (결측 {len(miss)})')
    print(df.tail(3).to_string())
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
