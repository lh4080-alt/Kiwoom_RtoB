# -*- coding: utf-8 -*-
"""시장 합계 외인 순매수 장기 이력 — ka10059 종목별 합산, 2014-07~ (2026-10-09 Lee 지시).

backfill_market_foreign.py(100일, 1회 호출/종목)를 과거로 페이지(dt를 최古일−1로 이동) 확장.
정의 동일: 외국인(기타외국인 제외), KOSPI 상장 주권만, 백만원 → 억원.
검증: 최근 100일은 기존 market_foreign.parquet와 일자별 일치해야 함 (불일치 시 덮어쓰지 않음).
출력: config/data/market_foreign_long.parquet (date, frgn_eok, n_codes) — 재개용 종목별 체크포인트 포함.
한계: 현재 상장 주권 기준(생존편향) — 과거 일수록 합산 종목 수(n_codes) 감소, 표기함.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
OUT = os.path.join(ROOT, 'config', 'data', 'market_foreign_long.parquet')
CKPT = os.path.join(ROOT, 'config', 'data', 'market_foreign_long_ckpt.json')
START = '20140701'


def _num(s):
    s = str(s or '0').replace('+', '').replace(',', '')
    if s.startswith('--'):
        s = '-' + s[2:]
    try:
        return float(s)
    except ValueError:
        return 0.0


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    from utils.rate_limiter import requests as kreq
    import utils.config as config
    from brief.build import stock_universe
    token = await get_semi_token()

    # KOSPI 주권 목록 = ka10066 KOSPI ∩ 상장 주권 (기존 백필과 동일 유니버스)
    univ = stock_universe()
    rows66, cont, nk = [], 'N', ''
    for _ in range(60):
        r = await kreq.post(config.get_host_url() + '/api/dostk/mrkcond',
                            headers={'Content-Type': 'application/json;charset=UTF-8',
                                     'authorization': f'Bearer {token}', 'cont-yn': cont,
                                     'next-key': nk, 'api-id': 'ka10066'},
                            json={'mrkt_tp': '001', 'amt_qty_tp': '1', 'trde_tp': '0', 'stex_tp': '1'})
        rows66 += (r.json() or {}).get('opaf_invsr_trde') or []
        cont, nk = r.headers.get('cont-yn', 'N'), r.headers.get('next-key', '')
        if cont != 'Y':
            break
    codes = sorted({str(x['stk_cd']).strip() for x in rows66} & univ)
    ck = json.load(open(CKPT, encoding='utf-8')) if os.path.exists(CKPT) else {}
    todo = [c for c in codes if c not in ck]
    print(f'[mf] KOSPI 주권 {len(codes)} | 남은 {len(todo)}', flush=True)

    for i, code in enumerate(todo):
        series, dt = {}, datetime.now().strftime('%Y%m%d')
        for _ in range(40):
            rows = []
            for attempt in range(3):
                try:
                    r = await kreq.post(config.get_host_url() + '/api/dostk/stkinfo',
                                        headers={'Content-Type': 'application/json;charset=UTF-8',
                                                 'authorization': f'Bearer {token}', 'cont-yn': 'N',
                                                 'next-key': '', 'api-id': 'ka10059'},
                                        json={'dt': dt, 'stk_cd': code, 'amt_qty_tp': '1',
                                              'trde_tp': '0', 'unit_tp': '1'})
                    rows = (r.json() or {}).get('stk_invsr_orgn') or []
                    break
                except Exception:
                    await asyncio.sleep(2 * (attempt + 1))
            new = {str(x['dt']): _num(x.get('frgnr_invsr')) for x in rows
                   if str(x['dt']) not in series}
            if not new:
                break
            series.update(new)
            oldest = min(new)
            if oldest <= START:
                break
            dt = (datetime.strptime(oldest, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
        ck[code] = {d: v for d, v in series.items() if d >= START}
        if (i + 1) % 50 == 0:
            json.dump(ck, open(CKPT, 'w', encoding='utf-8'))
            if (i + 1) % 300 == 0:
                token = await get_semi_token()
            print(f'[mf] 진행 {i + 1}/{len(todo)}', flush=True)
    json.dump(ck, open(CKPT, 'w', encoding='utf-8'))

    per = {}
    for code, s in ck.items():
        for d, v in s.items():
            a = per.setdefault(d, [0.0, 0])
            a[0] += v
            a[1] += 1
    df = pd.DataFrame([{'date': pd.Timestamp(d), 'frgn_eok': v[0] / 100, 'n_codes': v[1]}
                       for d, v in per.items()]).set_index('date').sort_index()
    df.to_parquet(OUT)
    # 기존 100일 이력과 대조
    short = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'market_foreign.parquet'))
    j = short.join(df, rsuffix='_long', how='inner')
    diff = (j['frgn_eok'] - j['frgn_eok_long']).abs()
    print(f'[mf] 완료 {len(df)}일 {df.index.min().date()}~{df.index.max().date()} | '
          f'기존 100일 대조: 공통 {len(j)}일, 최대차 {diff.max():.2f}억')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
