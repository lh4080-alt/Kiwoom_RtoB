# -*- coding: utf-8 -*-
"""시장 합계 외인 순매수 — 종목별 합산 백필 + 독립 검증 (2026-10-09 Lee 제안).

외부 원천(pykrx 시장 투자자별)이 막혀 있으므로, V1에서 네이버 대조를 통과한
ka10059 종목별 외인 순매수를 KOSPI 전 종목 합산해 시장 합계를 재구성한다.
  · 검증 1: 최종 세션 — Σ ka10059(종목별) vs ka10066 시장 합계 (같은 1,308종목, 같은 정의)
  · 검증 2: 종목 단위 — 최종 세션 ka10059 vs ka10066 종목별 값 일치율
  · 산출: config/data/market_foreign.parquet (date, frgn_eok, n_codes) — 약 100거래일
정의: 외국인(기타외국인 제외) = frgnr_invsr, 금액 백만원 → 억원 (/100)
"""
import asyncio
import json
import os
import sys
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
OUT = os.path.join(ROOT, 'config', 'data', 'market_foreign.parquet')
REPORT = os.path.join(ROOT, 'config', 'data', 'verify', 'v1_market_foreign.json')


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
    token = await get_semi_token()
    hdr = lambda api, cont='N', nk='': {  # noqa: E731
        'Content-Type': 'application/json;charset=UTF-8', 'authorization': f'Bearer {token}',
        'cont-yn': cont, 'next-key': nk, 'api-id': api}

    # ka10066 KOSPI — 종목 목록 + 종목별 외인 (최종 세션)
    rows66, cont, nk = [], 'N', ''
    for _ in range(60):
        r = await kreq.post(config.get_host_url() + '/api/dostk/mrkcond', headers=hdr('ka10066', cont, nk),
                            json={'mrkt_tp': '001', 'amt_qty_tp': '1', 'trde_tp': '0', 'stex_tp': '1'})
        rows66 += (r.json() or {}).get('opaf_invsr_trde') or []
        cont, nk = r.headers.get('cont-yn', 'N'), r.headers.get('next-key', '')
        if cont != 'Y':
            break
    f66 = {str(x['stk_cd']).strip(): _num(x['frgnr_invsr']) for x in rows66}
    codes = sorted(f66)
    sum66 = sum(f66.values()) / 100
    print(f'[ka10066] KOSPI {len(codes)}종목 | 시장 외인 합 {sum66:+,.1f}억')

    # ka10059 종목별 — 100거래일 이력 (1회 호출/종목)
    per_day, last_by_code, fails = {}, {}, []
    today = datetime.now().strftime('%Y%m%d')
    for i, code in enumerate(codes):
        try:
            r = await kreq.post(config.get_host_url() + '/api/dostk/stkinfo', headers=hdr('ka10059'),
                                json={'dt': today, 'stk_cd': code, 'amt_qty_tp': '1',
                                      'trde_tp': '0', 'unit_tp': '1'})
            rows = (r.json() or {}).get('stk_invsr_orgn') or []
            for x in rows:
                d = str(x['dt'])
                v = _num(x.get('frgnr_invsr'))
                per_day.setdefault(d, [0.0, 0])
                per_day[d][0] += v
                per_day[d][1] += 1
            if rows:
                last_by_code[code] = (str(rows[0]['dt']), _num(rows[0].get('frgnr_invsr')))
        except Exception as e:
            fails.append(code)
            if len(fails) <= 3:
                print(f'  {code} 실패: {e}')
        if (i + 1) % 200 == 0:
            print(f'  진행 {i + 1}/{len(codes)} (실패 {len(fails)})', flush=True)

    df = pd.DataFrame([{'date': pd.Timestamp(d), 'frgn_eok': v[0] / 100, 'n_codes': v[1]}
                       for d, v in per_day.items()]).set_index('date').sort_index()
    # 커버리지가 낮은 오래된 날짜(신규상장 등으로 종목수 부족)는 제외하지 않고 n_codes로 남김
    df.to_parquet(OUT)

    last_d = df.index.max()
    sum59_last = float(df.loc[last_d, 'frgn_eok'])
    match = sum(1 for c, (d, v) in last_by_code.items()
                if d == last_d.strftime('%Y%m%d') and abs(v - f66.get(c, 0)) < 0.5)
    rep = {'last_session': str(last_d.date()), 'sum_ka10059_eok': round(sum59_last, 1),
           'sum_ka10066_eok': round(sum66, 1), 'diff_eok': round(sum59_last - sum66, 1),
           'stock_match': f'{match}/{len(last_by_code)}', 'fails': len(fails),
           'days': int(len(df)), 'range': f'{df.index.min().date()}~{last_d.date()}'}
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    print(df.tail(5).to_string())
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
