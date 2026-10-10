# -*- coding: utf-8 -*-
"""Phase 5 시장 합계 — 시장 단위 원천 백필 (사전 선언 v1.1, 2026-10-10 Lee 지시).

종목 합산(현재 상장 941종목)은 상장폐지·신규상장 때문에 과거로 갈수록 시장 전체가 아니다
(2016-01-15 Σ주권/시장 = 0.874, 2025-01-15 = 0.708 — 실측). 시장 합계는 시장 단위 원천만 쓴다.
  수급   ka10051 업종별투자자순매수, 업종 001(종합 KOSPI), 단위 억원 — 거래일마다 1회 조회
  거래대금 ka20006 업종일봉 001 trde_prica, 단위 백만원
출력: config/data/market_flows_k51.parquet (index=date, 억원: frgn·natfor·orgn·ind·etc_corp·natn,
      tv_mil: 백만원). --update: 최근 거래일 중 누락분만 추가 (매일 갱신용)
"""
import asyncio
import os
import sys
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
OUT = os.path.join(ROOT, 'config', 'data', 'market_flows_k51.parquet')
START = pd.Timestamp('2014-07-01')
MAP = {'frgn': 'frgnr_netprps', 'natfor': 'native_trmt_frgnr_netprps', 'orgn': 'orgn_netprps',
       'ind': 'ind_netprps', 'etc_corp': 'etc_corp_netprps', 'natn': 'natn_netprps'}


def _num(s):
    s = str(s or '').replace('+', '').replace(',', '')
    try:
        return float(s)
    except ValueError:
        return float('nan')


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    from utils.rate_limiter import requests as kreq
    import utils.config as config
    token = await get_semi_token()
    hdr = lambda api, c='N', n='': {  # noqa: E731
        'Content-Type': 'application/json;charset=UTF-8', 'authorization': f'Bearer {token}',
        'cont-yn': c, 'next-key': n, 'api-id': api}

    # 거래대금 — ka20006 페이징 (최신 → 과거)
    tv, cont, nk = {}, 'N', ''
    for _ in range(8):
        r = await kreq.post(config.get_host_url() + '/api/dostk/chart', headers=hdr('ka20006', cont, nk),
                            json={'inds_cd': '001', 'base_dt': datetime.now().strftime('%Y%m%d')})
        for x in r.json().get('inds_dt_pole_qry') or []:
            tv[pd.Timestamp(str(x['dt']))] = _num(x['trde_prica'])
        cont, nk = r.headers.get('cont-yn', 'N'), r.headers.get('next-key', '')
        if cont != 'Y' or min(tv) <= START:
            break
    days = sorted(d for d in tv if d >= START)
    old = pd.read_parquet(OUT) if os.path.exists(OUT) else pd.DataFrame()
    have = set(old.index) if len(old) else set()
    todo = [d for d in days if d not in have]
    if '--update' not in sys.argv:
        print(f'[k51] 거래일 {len(days)} | 수집 대상 {len(todo)}', flush=True)
    rows = []
    for i, d in enumerate(todo):
        for attempt in range(3):
            try:
                r = await kreq.post(config.get_host_url() + '/api/dostk/sect', headers=hdr('ka10051'),
                                    json={'mrkt_tp': '0', 'amt_qty_tp': '0',
                                          'base_dt': d.strftime('%Y%m%d'), 'stex_tp': '1'})
                it = next(x for x in r.json().get('inds_netprps') or []
                          if str(x['inds_cd']).startswith('001'))
                rows.append({'date': d, **{k: _num(it.get(v)) for k, v in MAP.items()},
                             'tv_mil': tv[d]})
                break
            except Exception as e:
                if attempt == 2:
                    print(f'  {d.date()} 실패: {e}', flush=True)
                await asyncio.sleep(2 * (attempt + 1))
        if (i + 1) % 300 == 0:
            print(f'[k51] 진행 {i + 1}/{len(todo)}', flush=True)
            token = await get_semi_token()
    new = pd.DataFrame(rows).set_index('date') if rows else pd.DataFrame()
    df = pd.concat([old, new]).sort_index() if len(old) else new.sort_index()
    df = df[~df.index.duplicated(keep='last')]
    df.to_parquet(OUT)
    print(f'[k51] {datetime.now():%m-%d %H:%M} 저장 {len(df)}일 {df.index.min().date()}~{df.index.max().date()} '
          f'(신규 {len(rows)})', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
