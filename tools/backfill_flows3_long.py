# -*- coding: utf-8 -*-
"""Phase 5 데이터 — 3자 수급 장기 백필 (ka10059 확정치, KOSPI 상장 주권, 2014-07~).

종목별로 외국인(frgnr_invsr)·기타외국인(natfor)·기관계(orgn)·개인(ind_invsr)·기타법인(etc_corp)
순매수 금액과 거래대금(acc_trde_prica)을 수집한다. 단위 모두 백만원 (amt_qty_tp=1).
출력: config/data/flows3/<code>.parquet (종목별, 재개 가능) → 집계는 tools/flows3_build.py
한계: 현재 상장 주권 기준(생존편향). ka10059 외 소스 혼용 없음.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
OUT_DIR = os.path.join(ROOT, 'config', 'data', 'flows3')
START = '20140701'
FIELDS = {'frgn': 'frgnr_invsr', 'natfor': 'natfor', 'orgn': 'orgn', 'ind': 'ind_invsr',
          'etc_corp': 'etc_corp', 'tv': 'acc_trde_prica'}


def _num(s):
    s = str(s or '0').replace('+', '').replace(',', '')
    if s.startswith('--'):
        s = '-' + s[2:]
    try:
        return float(s)
    except ValueError:
        return float('nan')


async def fetch(code, token, kreq, config, pages: int = 40) -> pd.DataFrame:
    rows, dt = {}, datetime.now().strftime('%Y%m%d')
    for _ in range(pages):
        got = []
        for attempt in range(3):
            try:
                r = await kreq.post(config.get_host_url() + '/api/dostk/stkinfo',
                                    headers={'Content-Type': 'application/json;charset=UTF-8',
                                             'authorization': f'Bearer {token}', 'cont-yn': 'N',
                                             'next-key': '', 'api-id': 'ka10059'},
                                    json={'dt': dt, 'stk_cd': code, 'amt_qty_tp': '1',
                                          'trde_tp': '0', 'unit_tp': '1'})
                got = (r.json() or {}).get('stk_invsr_orgn') or []
                break
            except Exception:
                await asyncio.sleep(2 * (attempt + 1))
        new = {str(x['dt']): {k: _num(x.get(v)) for k, v in FIELDS.items()}
               for x in got if str(x['dt']) not in rows}
        if not new:
            break
        rows.update(new)
        oldest = min(new)
        if oldest <= START:
            break
        dt = (datetime.strptime(oldest, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    df = pd.DataFrame.from_dict({d: v for d, v in rows.items() if d >= START}, orient='index')
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    from utils.rate_limiter import requests as kreq
    import utils.config as config
    from brief.build import ka10066_official, stock_universe
    token = await get_semi_token()
    univ = stock_universe()
    off = await ka10066_official(token, univ)
    # KOSPI 주권 = ka10066 KOSPI(001) 응답 ∩ 상장 주권 — ka10066_official은 시장 구분을 안 남기므로 재조회
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
    os.makedirs(OUT_DIR, exist_ok=True)
    done = {f[:-8] for f in os.listdir(OUT_DIR) if f.endswith('.parquet')}
    if '--update' in sys.argv:
        # 일일 갱신 — 기존 종목은 최근 1페이지(약 100거래일) 병합, 신규 상장은 전체 수집
        n_new = 0
        for i, code in enumerate(codes):
            path = os.path.join(OUT_DIR, f'{code}.parquet')
            if code in done:
                old = pd.read_parquet(path)
                recent = await fetch(code, token, kreq, config, pages=1)
                df = pd.concat([old[~old.index.isin(recent.index)], recent]).sort_index()
            else:
                df = await fetch(code, token, kreq, config)
                n_new += 1
            df.to_parquet(path)
            if (i + 1) % 300 == 0:
                token = await get_semi_token()
        last = pd.read_parquet(os.path.join(OUT_DIR, '005930.parquet')).index.max().date()
        print(f'[flows3 update] {datetime.now():%m-%d %H:%M} {len(codes)}종목 (신규 {n_new}) | 최신 {last}', flush=True)
        return 0
    todo = [c for c in codes if c not in done]
    print(f'[flows3] KOSPI 주권 {len(codes)} | 남은 {len(todo)}', flush=True)
    for i, code in enumerate(todo):
        df = await fetch(code, token, kreq, config)
        df.to_parquet(os.path.join(OUT_DIR, f'{code}.parquet'))
        if (i + 1) % 50 == 0:
            if (i + 1) % 300 == 0:
                token = await get_semi_token()
            print(f'[flows3] 진행 {i + 1}/{len(todo)}', flush=True)
    print(f'[flows3] 완료 {len(codes)}종목', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
