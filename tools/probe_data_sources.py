# -*- coding: utf-8 -*-
"""§5 데이터 소스 프로브 — 최종작업지시서 1단계 (2026-10-07).

각 소스의 가능/불가 · 이력 시작일 · 필드 · 호출 제한을 실측해 표로 보고.
불가능하면 임의 대체 없이 "미확보"로 둔다 (지시서 규칙).

실행: beelink에서 python tools/probe_data_sources.py
"""
import asyncio
import os
import sys
import warnings

import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))

PROBE_DATES = ['20100104', '20150102', '20200102', '20250102']  # 이력 시작일 탐색용


async def probe_ka10059(token) -> dict:
    """삼전·하닉 종목별 투자자 수급 — ka10059 (dt 과거 조회)."""
    from utils.rate_limiter import requests
    import utils.config as config
    out = {'source': 'ka10059 (종목별투자자기관별)', 'status': '?', 'earliest': None,
           'fields': [], 'note': []}
    ok_days = []
    for d in PROBE_DATES:
        try:
            r = await requests.post(
                config.get_host_url() + '/api/dostk/stkinfo',
                headers={'Content-Type': 'application/json;charset=UTF-8',
                         'authorization': f'Bearer {token}', 'cont-yn': 'N',
                         'next-key': '', 'api-id': 'ka10059'},
                json={'dt': d, 'stk_cd': '005930', 'amt_qty_tp': '1',
                      'trde_tp': '0', 'unit_tp': '1'})
            body = r.json().get('stk_invsr_orgn') or []
            rows = [it for it in body if str(it.get('dt', '')).replace('-', '') == d]
            if rows:
                ok_days.append(d)
                if not out['fields']:
                    out['fields'] = sorted(rows[0].keys())
            else:
                out['note'].append(f'{d}: 행 없음/날짜 불일치')
        except Exception as e:
            out['note'].append(f'{d}: 오류 {e}')
        await asyncio.sleep(0.3)
    if ok_days:
        out['status'] = '가능'
        out['earliest'] = min(ok_days)
        out['note'].insert(0, f'프로브 성공: {ok_days}')
    else:
        out['status'] = '불가'
    return out


async def probe_ka10061(token) -> dict:
    """ka10061 종목별투자자기관별합계 — 보조 경로 확인."""
    from utils.rate_limiter import requests
    import utils.config as config
    out = {'source': 'ka10061 (종목별투자자기관별합계)', 'status': '?', 'earliest': '-',
           'fields': [], 'note': []}
    try:
        r = await requests.post(
            config.get_host_url() + '/api/dostk/stkinfo',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {token}', 'cont-yn': 'N',
                     'next-key': '', 'api-id': 'ka10061'},
            json={'dt': '20250102', 'stk_cd': '005930', 'amt_qty_tp': '1',
                  'trde_tp': '0', 'unit_tp': '1'})
        keys = [k for k in (r.json() or {}).keys()]
        out['status'] = '응답 확인 필요'
        out['fields'] = keys
    except Exception as e:
        out['status'] = '불가'
        out['note'].append(str(e))
    return out


def probe_pykrx() -> dict:
    """VKOSPI·예탁금 후보 — pykrx 설치 + KRX 로그인 자격."""
    out = {'source': 'pykrx (KRX)', 'status': '?', 'earliest': '-', 'fields': [], 'note': []}
    try:
        import pykrx  # noqa
        out['status'] = '설치됨'
    except ImportError:
        out['status'] = '미설치'
        out['note'].append('pip install pykrx 필요')
    has_id = bool(os.environ.get('KRX_ID')) and bool(os.environ.get('KRX_PW'))
    out['note'].append(f'KRX_ID/KRX_PW env: {"있음" if has_id else "없음"}')
    if not has_id:
        out['status'] = (out['status'] + ' — 로그인 불가') if out['status'] == '설치됨' else '불가(자격 없음)'
        out['note'].append('pykrx는 KRX 로그인 필요 (지시서 §5) — 자격 없으면 미확보')
    return out


def probe_external() -> list:
    """공공데이터포털/금투협/KRX MP — API 키 유무만 확인."""
    rows = []
    keys = {'공공데이터포털': 'DATA_GO_KR_KEY', 'KRX 데이터 마켓플레이스': 'KRX_MP_KEY',
            '금투협 FreeSIS': 'KIFRA_KEY'}
    for name, env in keys.items():
        has = bool(os.environ.get(env))
        rows.append({'source': name, 'status': '키 있음' if has else '미확보(API 키 없음)',
                     'earliest': '-', 'fields': [], 'note': [f'env {env}: {"있음" if has else "없음"}']})
    return rows


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    if not token:
        print('[probe] 토큰 발급 실패')
        return 1
    results = [await probe_ka10059(token), await probe_ka10061(token),
               probe_pykrx()] + probe_external()
    print()
    print(f'{"소스":<34}{"상태":<26}이력시작')
    print('-' * 80)
    for r in results:
        print(f"{r['source']:<34}{r['status']:<26}{r['earliest']}")
        for n in r['note']:
            print(f"   - {n}")
        if r['fields']:
            print(f"   - 필드({len(r['fields'])}): {', '.join(r['fields'][:12])}"
                  + (' …' if len(r['fields']) > 12 else ''))
    print()
    print('호출 제한: 키움 REST 공통 rate_limiter (기존 규칙), 수급 백필은 장 마감 후 확정치만 저장')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
