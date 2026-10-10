# -*- coding: utf-8 -*-
"""당일 봉 종가 확정 시각 탐침 (2026-10-10 Lee 지시 — 1회성, 10/12 15:35~21:00 15분 간격).

시각별로 ka10081 당일 봉 종가(KRX 무접미사·_NX·_AL)와 ka10066 cur_prc를 기록한다.
목적: 16:50 기록값이 최종 종가와 달랐던 원인 확정 + 20:30 실행 시 확정값·ka10066 날짜 확인.
기간: 10/12~10/16 5거래일 (Lee 보완 — 오류가 특정 기간에 몰려 하루로는 재현 안 될 수 있음, 연휴 직후 포함).
수정주가 구분(upd_stkpc_tp) 0·1을 모두 기록 — 시각·거래소로 설명 안 되면 다음 후보.
출력: config/data/verify/close_timing_probe.jsonl (읽기·기록 외 동작 없음)
"""
import asyncio
import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
OUT = os.path.join(BASE, '..', 'config', 'data', 'verify', 'close_timing_probe.jsonl')
CODES = ('005930', '000660', '455850')


async def main() -> int:
    from api.daily_candle import fn_ka10081
    from modules.semi_trigger.token_provider import get_semi_token
    from utils.rate_limiter import requests
    import utils.config as config
    t = await get_semi_token()
    today = datetime.now().strftime('%Y%m%d')
    rec = {'at': datetime.now().isoformat(timespec='seconds'), 'k81': {}, 'k66': {}}
    for c in CODES:
        for suf, upd in (('', '1'), ('', '0'), ('_NX', '1'), ('_AL', '1')):
            key = f'{c}{suf}|upd{upd}'
            try:
                r = await fn_ka10081(c + suf, base_dt=today, upd_stkpc_tp=upd, token=t, silent=True)
                cs = r.get('candles') or [{}]
                rec['k81'][key] = {'date': str(cs[0].get('date')), 'close': cs[0].get('close'),
                                   'prev_date': str(cs[1].get('date')) if len(cs) > 1 else None,
                                   'prev_close': cs[1].get('close') if len(cs) > 1 else None}
            except Exception as e:
                rec['k81'][key] = {'error': str(e)[:60]}
    try:
        rows, cont, nk = [], 'N', ''
        for _ in range(40):
            r = await requests.post(config.get_host_url() + '/api/dostk/mrkcond',
                                    headers={'Content-Type': 'application/json;charset=UTF-8',
                                             'authorization': f'Bearer {t}', 'cont-yn': cont,
                                             'next-key': nk, 'api-id': 'ka10066'},
                                    json={'mrkt_tp': '001', 'amt_qty_tp': '1', 'trde_tp': '0', 'stex_tp': '1'})
            rows += r.json().get('opaf_invsr_trde') or []
            cont, nk = r.headers.get('cont-yn', 'N'), r.headers.get('next-key', '')
            if cont != 'Y':
                break
        rec['k66'] = {str(it.get('stk_cd')).strip(): {'cur_prc': it.get('cur_prc'), 'frgnr': it.get('frgnr_invsr')}
                      for it in rows if str(it.get('stk_cd')).strip() in CODES}
    except Exception as e:
        rec['k66'] = {'error': str(e)[:60]}
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
