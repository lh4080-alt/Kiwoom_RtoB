# -*- coding: utf-8 -*-
"""macro_monitor.jsonl 전 필드 오염 스캔 + 무효 플래그 (2026-10-10 Lee 지시 — 삭제·수정 없음).

필드별 독립 원천과 대조해 어긋나면 행에 invalid[필드] = 사유를 기록한다 (값은 그대로 둠).
읽는 쪽은 macro_monitor.load_history()가 플래그 필드를 None으로 가려서 돌려준다 (디스크 값 보존).
  kospi_ret        ka20006 종가 재계산 (허용 0.02%p)
  semis_detail · others_detail   ka10081 확정 종가 일간 수익률, 종목·ETF별 (허용 0.05%p)
  semis_ret        삼전·하닉 평균(v2) — 3종목 평균(v1)과도 맞지 않을 때만 (허용 0.05%p)
  others_ret · rotation_spread   ka10081 재계산 평균·차 (허용 0.05%p)
  breadth · breadth_v2   MDC 패널 정의(무수정주가·ETF 포함) — 폐기된 정의라 전 행
  corr             입력 필드가 창(CORR_WINDOW+10행) 안에서 하나라도 무효면 해당 상관 키
  foreign_net_eok  ka10051 업종 001 외국인 (허용 max(1%, 50억)) — ka10058·16종목합·ETN 혼입 기록 판별
  flows(종목별)    ka10059 외국인 (허용 max(5%, 100억)) — 10/7 스캔 방식 재사용
  breadth          동결 검출: 전일과 advancers·pct_above_ma200 동일 + panel_last_date ≠ 행 날짜
  regime.label     ka20006 point-in-time 재계산 라벨과 불일치
리포트: config/data/verify/jsonl_scan.csv / 백업: macro_monitor.jsonl.bak_scan_<일시>
미대조(독립 원천 없음·정의 상이): nq_overnight, usdkrw(·_ret), us10y, short_term(ka20006 파생), axes
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
    from macro_monitor import SEMIS, OTHER_ETFS
    for code, name in SEMIS + OTHER_ETFS:
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
    from macro_monitor import JSONL_PATH, compute_regime
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    k = pd.read_parquet(os.path.join(D, 'kospi_daily_ohlc.parquet'))['close'].astype(float)
    k.index = pd.DatetimeIndex(k.index)
    kret = k.pct_change() * 100
    k51 = pd.read_parquet(os.path.join(D, 'market_flows_k51.parquet'))['frgn']
    srets = await stock_rets(token)
    f59 = {c: await ka10059_frgn(token, c) for c in ('005930', '000660')}

    hist = [json.loads(l) for l in open(JSONL_PATH, encoding='utf-8') if l.strip()]  # 원본 (가림 없이)
    report, flagged, hist_inv = [], {}, {}
    prev = None
    for r in hist:
        d = pd.Timestamp(r['date'])
        inv = {}
        if r.get('kospi_ret') is not None and d in kret.index:
            if abs(r['kospi_ret'] - kret[d]) > 0.02:
                inv['kospi_ret'] = f"ka20006 재계산 {kret[d]:+.2f} vs 기록 {r['kospi_ret']:+.2f}"
        for fld in ('semis_detail', 'others_detail'):
            for nm, v in (r.get(fld) or {}).items():
                s = srets.get(nm)
                if s is not None and d in s.index and v is not None and abs(v - s[d]) > 0.05:
                    inv.setdefault(fld, '')
                    inv[fld] += f"{nm} ka10081 {s[d]:+.2f} vs {v:+.2f}; "
        at = lambda nms: [srets[n][d] for n in nms if n in srets and d in srets[n].index  # noqa: E731
                          and not np.isnan(srets[n][d])]
        pair, tri = at(('삼성전자', 'SK하이닉스')), at(('삼성전자', 'SK하이닉스', '소부장ETF'))
        oth = at([n for _, n in OTHER_ETFS])
        sr = r.get('semis_ret')
        if sr is not None and len(pair) == 2:
            refs = [sum(pair) / 2] + ([sum(tri) / 3] if len(tri) == 3 else [])
            if min(abs(sr - x) for x in refs) > 0.05:
                inv['semis_ret'] = f"ka10081 재계산 {refs[0]:+.2f} vs 기록 {sr:+.2f}"
        orr = r.get('others_ret')
        if orr is not None and oth and abs(orr - sum(oth) / len(oth)) > 0.05:
            inv['others_ret'] = f"ka10081 재계산 {sum(oth) / len(oth):+.2f} vs 기록 {orr:+.2f}"
        rs = r.get('rotation_spread')
        if rs is not None and len(pair) == 2 and oth:
            ref = sum(pair) / 2 - sum(oth) / len(oth)
            if abs(rs - ref) > 0.05:
                inv['rotation_spread'] = f"ka10081 재계산 {ref:+.2f} vs 기록 {rs:+.2f}"
        for fld in ('breadth', 'breadth_v2'):
            if r.get(fld) and fld not in inv:
                inv[fld] = 'MDC 패널 정의(무수정주가·ETF 포함) — 폐기 정의, k81 패널로 대체'
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
            inv['breadth'] = f"동결 — panel_last {b.get('panel_last_date')}; MDC 패널 정의 — 폐기"
        reg = (r.get('regime') or {}).get('label')
        if reg:
            sub = k[k.index <= d].iloc[-600:]
            rr = compute_regime({x.strftime('%Y-%m-%d'): v for x, v in sub.items()})
            if rr and rr['label'] != reg:
                inv['regime'] = f"ka20006 재계산 {rr['label']} vs 기록 {reg}"
        if inv:
            flagged[r['date']] = inv
            hist_inv[r['date']] = inv
            for fld, why in inv.items():
                report.append({'date': r['date'], 'field': fld, 'reason': why.strip('; ')})
        prev = r

    # 파생: corr — 입력이 창 안에서 무효면 해당 키 무효 (macro_monitor.rolling_corr 창과 동일)
    from macro_monitor import CORR_WINDOW
    corr_in = {'kospi_nq': ('kospi_ret', 'nq_overnight'), 'kospi_usd': ('kospi_ret', 'usdkrw_ret'),
               'semis_others': ('semis_ret', 'others_ret'), 'rotation_kospi': ('rotation_spread', 'kospi_ret')}
    for i, r in enumerate(hist):
        win = [x['date'] for x in hist[max(0, i - CORR_WINDOW - 10):i + 1]]
        bad = [k for k, ins in corr_in.items() if (r.get('corr') or {}).get(k) is not None
               and any(f in hist_inv.get(w, {}) for w in win for f in ins)]
        if bad:
            why = '입력 무효 포함 창: ' + ','.join(bad)
            flagged.setdefault(r['date'], {})['corr'] = why
            report.append({'date': r['date'], 'field': 'corr', 'reason': why})

    backup = JSONL_PATH + f'.bak_scan_{datetime.now():%Y%m%d_%H%M%S}'
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
