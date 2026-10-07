# -*- coding: utf-8 -*-
"""잔류 오염 전체 스캔 — jsonl flows 전 행을 ka10059로 대조 (2026-10-07 Lee 지시).

upsert 병합이 None 정리를 무시하는 특성으로 일부 행에 옛 값이 남았을 가능성 전수 확인.
읽기 전용 — 수정 없이 보고만 (자동 보정 금지 원칙).
실행: beelink에서 python tools/scan_flow_pollution.py
"""
import asyncio
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))


async def main() -> int:
    from macro_monitor import load_history
    from utils.rate_limiter import requests
    import utils.config as config
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()

    async def fetch(code, d):
        r = await requests.post(
            config.get_host_url() + '/api/dostk/stkinfo',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {token}', 'cont-yn': 'N',
                     'next-key': '', 'api-id': 'ka10059'},
            json={'dt': d, 'stk_cd': code, 'amt_qty_tp': '1',
                  'trde_tp': '0', 'unit_tp': '1'})
        for it in (r.json() or {}).get('stk_invsr_orgn') or []:
            if str(it.get('dt', '')).replace('-', '') == d:
                return float(str(it.get('frgnr_invsr', '0')).replace('+', '')) / 100
        return None

    rows = [r for r in load_history() if r.get('flows')]
    print(f'스캔 대상 {len(rows)}행')
    bad = []
    for i, r in enumerate(rows):
        d = r['date']
        for code in ('005930', '000660'):
            stored = (r.get('flows') or {}).get(code, {}).get('frgnr')
            v = await fetch(code, d)
            if stored is None or v is None:
                bad.append((d, code, stored, v, '검증 불가'))
            elif abs(stored - v) > max(abs(v) * 0.05, 100):
                bad.append((d, code, stored, round(v, 1), '불일치'))
        if (i + 1) % 20 == 0:
            print(f'  진행 {i + 1}/{len(rows)}', flush=True)
        await asyncio.sleep(0.05)
    print()
    if bad:
        print(f'불일치 {len(bad)}건:')
        for d, code, stored, v, why in bad:
            print(f'  {d} {code}: jsonl={stored} vs ka10059={v} ({why})')
    else:
        print('전 행 일치 — 잔류 오염 없음')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
