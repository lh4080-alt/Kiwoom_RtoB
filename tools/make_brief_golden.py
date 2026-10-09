# -*- coding: utf-8 -*-
"""V3 골든 픽스처 생성 — 각 수치를 V1(외부)·V2(독립 구현)로 대조 통과한 경우에만 고정.

  python tools/make_brief_golden.py 2026-10-08T05:35 2026-10-09T05:35 [2026-10-10T05:35 ...]
출력: tests/fixtures/brief_<YYYYMMDD>.json  (frozen d · 기대 렌더 텍스트 · 대조표)
"""
import asyncio
import json
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import requests

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
FIX = os.path.join(ROOT, 'tests', 'fixtures')
H = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}


def verify(d: dict) -> list:
    """수치별 외부·독립 대조 → [(항목, 브리프값, 대조값, 허용, 통과)]."""
    import ta
    import yfinance as yf
    from pykrx import stock
    import brief_config as cfg
    sf = d['frame']
    p = sf['prev_kr']
    rows = []

    def add(item, a, b, tol):
        ok = a is not None and b is not None and abs(a - b) <= tol
        rows.append({'item': item, 'brief': a, 'ref': b, 'tol': tol, 'pass': bool(ok)})

    # KOSPI 국면 수치 — yfinance ^KS11 (V1 외부)
    y = yf.Ticker('^KS11').history(period='3y', interval='1d', auto_adjust=False)['Close']
    y.index = [x.date() for x in pd.DatetimeIndex(y.index).tz_localize(None)]
    y = y[[x <= p for x in y.index]]
    add('KOSPI 고점대비(%)', d['regime']['dd'], round((y.iloc[-1] / y.iloc[-250:].max() - 1) * 100, 1), 0.1)
    # ATR% — ta 라이브러리 (V2 독립 구현)
    k = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'kospi_daily_ohlc.parquet'))
    k.index = [x.date() for x in pd.DatetimeIndex(k.index)]
    k = k[[x <= p for x in k.index]]
    atr = ta.volatility.AverageTrueRange(k['high'], k['low'], k['close'], window=14).average_true_range()
    add('ATR14(%)', d['atr']['pct'], float(atr.iloc[-1] / k['close'].iloc[-1] * 100), 0.01)
    # 종목 MA 괴리 — pykrx (V1 외부)
    for code in ('005930', '000660'):
        px = stock.get_market_ohlcv('20240101', p.strftime('%Y%m%d'), code, adjusted=True)['종가']
        c = px.astype(float)
        add(f'{code} MA60(%)', d['semi'][code]['ma60'], float((c.iloc[-1] / c.iloc[-60:].mean() - 1) * 100), 0.01)
        add(f'{code} MA200(%)', d['semi'][code]['ma200'], float((c.iloc[-1] / c.iloc[-200:].mean() - 1) * 100), 0.01)
    # 가중 US 누적 — Nasdaq 원종가 수작업 (V1 외부 + V2 픽스처 방식)
    nas = {}
    for s in ('MU', 'SNDK'):
        js = requests.get(f'https://api.nasdaq.com/api/quote/{s}/historical?assetclass=stocks'
                          f'&fromdate=2026-08-01&todate={datetime.now():%Y-%m-%d}&limit=120',
                          headers=H, timeout=30).json()
        nas[s] = pd.Series({pd.Timestamp(x['date']).date():
                            float(x['close'].replace('$', '').replace(',', ''))
                            for x in js['data']['tradesTable']['rows']}).sort_index()
    sess = d['sessions_used']
    for code, ws in cfg.STOCK_US_WEIGHTS.items():
        hand = 0.0
        for s, w in ws.items():
            ser = nas[s]
            base = ser[[x < sess[0] for x in ser.index]].iloc[-1]
            hand += w * (ser.loc[sess[-1]] / base - 1) * 100
        add(f'{code} 가중 US 누적(%)', d['semi'][code]['x'], float(hand), 0.02)
    # 200일선 위 비율 — numpy 독립 재구현 (같은 원천, 다른 구현)
    from brief.build import stock_universe
    panel = pd.read_parquet(os.path.join(ROOT, 'config', 'data', cfg.BREADTH_PANEL))
    univ = stock_universe()
    cols = [c for c in panel.columns if c in univ]
    arr = panel[cols].copy()
    arr.index = [x.date() for x in pd.DatetimeIndex(arr.index)]
    arr = arr[[x <= p for x in arr.index]].values
    if not d.get('replay'):
        rows.append({'item': '200일선 위 비율(%)', 'brief': d['breadth']['pct_above200'],
                     'ref': None, 'tol': None, 'pass': True,
                     'note': '최종일 ka10066 확정 대체 — 독립 재구현은 재생 시점에서만 비교'})
    else:
        last = arr[-1]
        ma = np.nanmean(arr[-200:], axis=0)
        cnt = np.sum(~np.isnan(arr[-200:]), axis=0)
        valid = (~np.isnan(last)) & (cnt == 200)
        add('200일선 위 비율(%)', d['breadth']['pct_above200'],
            float(np.sum(last[valid] > ma[valid]) / valid.sum() * 100), 0.01)
        # A/D 20일 비율 — 같은 원천, numpy 부호 집계로 재구현 (기준점 이후 행만)
        o = pd.Timestamp(cfg.AD_ORIGIN).date()
        dates_ = [x for x in pd.DatetimeIndex(panel.index).date if o <= x <= p]
        sub = panel[cols].copy()
        sub.index = list(pd.DatetimeIndex(sub.index).date)
        a2 = sub.loc[dates_].values
        dif = a2[1:] / a2[:-1] - 1
        up_n = np.sum(dif > 0, axis=1)[-20:].sum()
        dn_n = np.sum(dif < 0, axis=1)[-20:].sum()
        add('A/D 20일 비율', d['breadth']['ad_ratio20'], float(up_n / dn_n), 1e-6)
    return rows


async def make(now_s: str):
    from brief.build import KST, collect, render
    from brief.integrity import run_checks
    from brief.serde import dumps
    now = datetime.fromisoformat(now_s).replace(tzinfo=KST)
    d = await collect(now)
    checks = verify(d)
    fails = run_checks(d)
    text = render(d, {}, fails)
    ok = all(r['pass'] for r in checks)
    for r in checks:
        print(f"  {'✓' if r['pass'] else '✗'} {r['item']}: 브리프 {r['brief']} | 대조 {r['ref']} (허용 {r['tol']})")
    if not ok:
        print(f'[golden] {now_s} — 대조 실패 항목 있음: 고정하지 않음')
        return False
    os.makedirs(FIX, exist_ok=True)
    out = os.path.join(FIX, f"brief_{now:%Y%m%d}.json")
    with open(out, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'now': now_s, 'd': json.loads(dumps(d)), 'fails': fails,
                            'text': text, 'verification': checks},
                           ensure_ascii=False, indent=1, default=str))
    print(f'[golden] 저장 {out}\n{text}\n')
    return True


async def main():
    ok = True
    for s in sys.argv[1:]:
        print(f'== {s} ==')
        ok &= await make(s)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
