# -*- coding: utf-8 -*-
"""macro_monitor.jsonl 전 필드 오염 스캔 + 무효 플래그 (2026-10-10 Lee 지시 — 삭제·수정 없음).

필드별 독립 원천과 대조해 어긋나면 행에 invalid[필드] = 사유를 기록한다 (값은 그대로 둠).
읽는 쪽은 brief.validity.valid_value()로 플래그 값을 건너뛴다.
  kospi_ret        ka20006 종가 재계산 (허용 0.02%p)
  semis_detail     ka10081 수정주가 일간 수익률 (허용 0.05%p)
  foreign_net_eok  ka10051 업종 001 외국인 (허용 max(1%, 50억)) — ka10058·16종목합·ETN 혼입 기록 판별
  flows(종목별)    ka10059 외국인 (허용 max(5%, 100억)) — 10/7 스캔 방식 재사용
  breadth          동결 검출: 전일과 advancers·pct_above_ma200 동일 + panel_last_date ≠ 행 날짜
  regime.label     ka20006 point-in-time 재계산 라벨과 불일치
리포트: config/data/verify/jsonl_scan.csv / 백업: macro_monitor.jsonl.bak_scan_<날짜>
"""
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
D = os.path.join(ROOT, 'config', 'data')


async def stock_rets(token) -> dict:
    from api.daily_candle import fn_ka10081
    out = {}
    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        r = await fn_ka10081(code, base_dt=datetime.now().strftime('%Y%m%d'), token=token, silent=True)
        c = pd.Series({pd.Timestamp(str(x['date'])): float(x['close']) for x in r['candles']}).sort_index()
        out[name] = c.pct_change() * 100
    return out


async def ka10059_frgn(token, code: str) -> pd.Series:
    from utils.rate_limiter import requests as kreq
    import utils.config as config
    r = await kreq.post(config.get_host_url() + '/api/dostk/stkinfo',
                        headers={'Content-Type': 'application/json;charset=UTF-8',
                                 'authorization': f'Bearer {token}', 'cont-yn': 'N', 'next-key': '',
                                 'api-id': 'ka10059'},
                        json={'dt': datetime.now().strftime('%Y%m%d'), 'stk_cd': code,
                              'amt_qty_tp': '1', 'trde_tp': '0', 'unit_tp': '1'})
    rows = (r.json() or {}).get('stk_invsr_orgn') or []
    return pd.Series({pd.Timestamp(str(x['dt'])): float(str(x['frgnr_invsr']).replace('+', '')) / 100
                      for x in rows})


async def main() -> int:
    from macro_monitor import JSONL_PATH, compute_regime, load_history
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    k = pd.read_parquet(os.path.join(D, 'kospi_daily_ohlc.parquet'))['close'].astype(float)
    k.index = pd.DatetimeIndex(k.index)
    kret = k.pct_change() * 100
    k51 = pd.read_parquet(os.path.join(D, 'market_flows_k51.parquet'))['frgn']
    srets = await stock_rets(token)
    f59 = {c: await ka10059_frgn(token, c) for c in ('005930', '000660')}

    hist = load_history()
    report, flagged = [], {}
    prev = None
    for r in hist:
        d = pd.Timestamp(r['date'])
        inv = {}
        if r.get('kospi_ret') is not None and d in kret.index:
            if abs(r['kospi_ret'] - kret[d]) > 0.02:
                inv['kospi_ret'] = f"ka20006 재계산 {kret[d]:+.2f} vs 기록 {r['kospi_ret']:+.2f}"
        for nm, v in (r.get('semis_detail') or {}).items():
            s = srets.get(nm)
            if s is not None and d in s.index and v is not None and abs(v - s[d]) > 0.05:
                inv.setdefault('semis_detail', '')
                inv['semis_detail'] += f"{nm} ka10081 {s[d]:+.2f} vs {v:+.2f}; "
        fn = r.get('foreign_net_eok')
        if fn is not None:
            ref = k51.get(d)
            if ref is None or np.isnan(ref):
                inv['foreign_net_eok'] = 'ka10051 대조값 없음'
            elif abs(fn - ref) > max(abs(ref) * 0.01, 50):
                inv['foreign_net_eok'] = f'ka10051 {ref:+,.0f} vs 기록 {fn:+,.0f}'
        for code, st in (r.get('flows') or {}).items():
            ref = f59.get(code, pd.Series(dtype=float)).get(d)
            v = (st or {}).get('frgnr')
            if ref is not None and v is not None and abs(v - ref) > max(abs(ref) * 0.05, 100):
                inv.setdefault('flows', '')
                inv['flows'] += f"{code} ka10059 {ref:+,.0f} vs {v:+,.0f}; "
        b = r.get('breadth') or {}
        if prev and b and (b.get('advancers') == (prev.get('breadth') or {}).get('advancers')
                           and b.get('pct_above_ma200') == (prev.get('breadth') or {}).get('pct_above_ma200')
                           and b.get('panel_last_date') not in (None, d.strftime('%Y-%m-%d'))):
            inv['breadth'] = f"동결 — panel_last {b.get('panel_last_date')}"
        reg = (r.get('regime') or {}).get('label')
        if reg:
            sub = k[k.index <= d].iloc[-600:]
            rr = compute_regime({x.strftime('%Y-%m-%d'): v for x, v in sub.items()})
            if rr and rr['label'] != reg:
                inv['regime'] = f"ka20006 재계산 {rr['label']} vs 기록 {reg}"
        if inv:
            flagged[r['date']] = inv
            for fld, why in inv.items():
                report.append({'date': r['date'], 'field': fld, 'reason': why.strip('; ')})
        prev = r

    backup = JSONL_PATH + f'.bak_scan_{datetime.now():%Y%m%d}'
    shutil.copy2(JSONL_PATH, backup)
    rows = [json.loads(l) for l in open(JSONL_PATH, encoding='utf-8') if l.strip()]
    for r in rows:
        if r['date'] in flagged:
            r['invalid'] = {**(r.get('invalid') or {}), **flagged[r['date']]}
    with open(JSONL_PATH + '.tmp', 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(JSONL_PATH + '.tmp', JSONL_PATH)
    rep = pd.DataFrame(report)
    rep.to_csv(os.path.join(D, 'verify', 'jsonl_scan.csv'), index=False, encoding='utf-8-sig')
    print(f'스캔 {len(hist)}행 | 플래그 행 {len(flagged)} | 백업 {os.path.basename(backup)}')
    if len(rep):
        print(rep.groupby('field').agg(n=('date', 'size'), 처음=('date', 'min'), 끝=('date', 'max')).to_string())
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
