# -*- coding: utf-8 -*-
"""Phase 2 수집 백필 — ka10051 업종별투자자순매수로 코스피 시장 전체
외인/기관계/개인/사모펀드 일별 순매수를 소급 수집 (2026-10-06 Lee 지시 Task B).

ka10051: base_dt 지원, inds_cd='001'(종합 KOSPI) 행에 투자자 전부 포함.
단위: amt_qty_tp='0'(금액, 백만원 추정) → /100 = 억원. 첫 응답에서 자릿수 검증.

저장: config/data/investor_flows.parquet (index=date, 억원)
거래일: kospi_daily_ohlc.parquet의 날짜 (이미 2012~2026 확보)

실행: beelink에서 python tools/backfill_investor_flows.py [시작일 YYYYMMDD]
"""
import asyncio
import os
import sys
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
OUT = os.path.join(BASE, '..', 'config', 'data', 'investor_flows.parquet')
OHLC_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')

FIELDS = {'frgnr_netprps': 'frgnr_eok', 'orgn_netprps': 'orgn_eok',
          'ind_netprps': 'ind_eok', 'samo_fund_netprps': 'samo_eok'}


def _num(s):
    s = str(s or '0').replace('+', '')
    if s.startswith('--'):
        s = '-' + s[2:]
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


async def fetch_day(token, d):
    from utils.rate_limiter import requests
    import utils.config as config
    r = await requests.post(
        config.get_host_url() + '/api/dostk/sect',
        headers={'Content-Type': 'application/json;charset=UTF-8',
                 'authorization': f'Bearer {token}', 'cont-yn': 'N',
                 'next-key': '', 'api-id': 'ka10051'},
        json={'mrkt_tp': '0', 'amt_qty_tp': '0', 'base_dt': d, 'stex_tp': '1'})
    data = r.json()
    for it in data.get('inds_netprps') or []:
        cd = str(it.get('inds_cd', ''))
        if cd.rstrip('_AL').rstrip('_NX') == '001':
            # 천만원 → 억원 (실측 확정 2026-10-06: 전 투자자 합=0 정합 + 자릿수 검증.
            # 예: 9/29 외인 raw -29,944 → -2,994억. PDF 미명시라 실측으로 확정)
            return {out: round(_num(it.get(src)) / 10, 1) for src, out in FIELDS.items()}
    return None


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    from utils.rate_limiter import requests
    token = await get_semi_token()
    if not token:
        print('[flows] 토큰 발급 실패')
        return 1

    start = sys.argv[1] if len(sys.argv) > 1 else '20210104'
    ohlc = pd.read_parquet(OHLC_PATH)
    days = [d.strftime('%Y%m%d') for d in ohlc.index if d.strftime('%Y%m%d') >= start]
    print(f'[flows] 대상 {len(days)}일 ({days[0]}~{days[-1]})')

    # 기존 결과 있으면 이어받기
    done = {}
    if os.path.exists(OUT):
        old = pd.read_parquet(OUT)
        done = {d.strftime('%Y%m%d'): row for d, row in old.iterrows()}
        print(f'[flows] 기존 {len(done)}일 이어받기')

    rows, miss = {}, []
    for i, d in enumerate(days):
        if d in done:
            rows[d] = dict(done[d])
            continue
        try:
            got = await fetch_day(token, d)
            if got:
                rows[d] = got
            else:
                miss.append(d)
        except Exception as e:
            miss.append(d)
            if len(miss) <= 3:
                print(f'[flows] {d} 실패: {e}')
        if (i + 1) % 100 == 0:
            print(f'[flows] 진행 {i + 1}/{len(days)} (결측 {len(miss)})', flush=True)
            await asyncio.sleep(0.3)

    df = pd.DataFrame.from_dict(rows, orient='index').sort_index()
    df.index.name = 'date'
    df.to_parquet(OUT)
    print(f'[flows] 저장 {len(df)}일 → {OUT} (결측 {len(miss)})')
    print('[flows] 최근 5일:')
    print(df.tail(5).to_string())
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
