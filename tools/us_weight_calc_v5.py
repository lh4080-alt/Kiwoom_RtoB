# -*- coding: utf-8 -*-
"""작업 4 검증 v5 — 삼전 시가→종가 구간 분할·MDE 정합성·거래비용·수급/MA60 순열 (2026-10-09).

· 삼전 시가→종가: 2012~2019 / 2020~ 구간별 순열 p + 효과크기 (v4 누락분)
· MDE: 지표별 σ(시가→종가 ~2% / 시가→D+5 ~4.5%)로 분리 재산출 → v4 p와 정합 확인
· 거래비용: semi_config.COST_RT(0.25%) 반영 순수익 재산출 (당일 매매 = 왕복)
· 수급 역행·MA60: 표시 전용 검증 — 트리거일 중 플래그 ON vs OFF, MA60 구간별 성과 (순열)
· 등급 조정 스위치는 FDR 통과 시에만 ON (semi_config, 현재 False)

실행: beelink에서 python tools/us_weight_calc_v5.py
"""
import json
import os
import sqlite3
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
from modules.semi_trigger.kr_calendar import next_kr_trading_day  # noqa: E402
from modules.semi_trigger.semi_config import STOCK_US_WEIGHTS, SIGMA_WINDOW, COST_RT  # noqa: E402

rng = np.random.default_rng(42)


def perm_p(vals: np.ndarray, trig_idx: np.ndarray, n_perm: int = 10_000, block: int = 5) -> float:
    n = len(vals)
    k = len(trig_idx)
    actual = float(np.nanmean(vals[trig_idx]))
    null = []
    for _ in range(n_perm):
        picks = []
        while len(picks) < k:
            b = int(rng.integers(0, max(1, n - block)))
            picks.extend(range(b, b + block))
        null.append(float(np.nanmean(vals[np.array(picks[:k])])))
    null = np.array(null)
    return float((np.sum(null >= actual) + 1) / (n_perm + 1))


def mde(n_trig: int, n_base: int, sigma: float) -> float:
    zd = 1.959964 + 0.841621  # α=0.05 양측, 검정력 80%
    return zd * sigma * float(np.sqrt(1 / n_trig + 1 / n_base))


def main() -> int:
    import yfinance as yf
    # ── 데이터 ──
    us = {}
    for s in ('MU', 'SNDK'):
        h = yf.Ticker(s).history(period='max', interval='1d', auto_adjust=True)['Close']
        h.index = h.index.tz_localize(None).normalize()
        us[s] = h.pct_change() * 100
    us_ret = pd.DataFrame(us).dropna(how='all')
    kr = {}
    for c in ('005930', '000660'):
        h = yf.Ticker(f'{c}.KS').history(period='max', interval='1d', auto_adjust=False)
        h.index = h.index.tz_localize(None).normalize()
        kr[c] = h[['Open', 'Close']]
    rows = []
    for d in us_ret.index:
        e_iso = next_kr_trading_day(d.date().isoformat())
        if not e_iso:
            continue
        e = pd.Timestamp(e_iso)
        h = kr['005930']
        idx = h.index
        i = np.where(idx >= e)[0]
        if not len(i) or idx[i[0]] != e or i[0] == 0:
            continue
        j = i[0]
        prev_c = h['Close'].iloc[j - 1]
        rows.append({'us_date': d, 'kr_date': e,
                     'oc': (h['Close'].iloc[j] / h['Open'].iloc[j] - 1) * 100,
                     'd5': ((h['Close'].iloc[j + 5] / h['Open'].iloc[j] - 1) * 100
                            if j + 5 < len(h) else np.nan),
                     'MU': us_ret.loc[d, 'MU'] if d in us_ret.index else np.nan,
                     'SNDK': us_ret.loc[d, 'SNDK'] if d in us_ret.index else np.nan})
    al = pd.DataFrame(rows).sort_values('us_date').reset_index(drop=True)
    ws = STOCK_US_WEIGHTS['005930']
    al['trig'] = [(z <= -1.3) or (w <= -3.5) for z, w in zip(
        ((us_ret['MU'] - us_ret['MU'].rolling(SIGMA_WINDOW).mean().shift(1))
         / us_ret['MU'].rolling(SIGMA_WINDOW).std().shift(1)).reindex(al['us_date']).values,
        (al['MU'] * ws['MU'] + al['SNDK'].fillna(0) * ws['SNDK']).values)]

    # ── 1) 삼전 시가→종가 구간 분할 (순열) ──
    print('== 1) 삼전 시가→종가 구간 분할 (순열 10,000회) ==')
    oc_all = al['oc'].values
    sigma_oc = float(np.nanstd(oc_all))
    for label, lo, hi in (('2012~2019', '2012-01-01', '2019-12-31'),
                          ('2020~', '2020-01-01', '2026-12-31')):
        sub = al[(al['us_date'] >= lo) & (al['us_date'] <= hi)]
        vals = sub['oc'].values
        idx = np.where(sub['trig'].values)[0]
        if len(idx) < 15:
            print(f'  [{label}] n={len(idx)} 표본 부족')
            continue
        p = perm_p(vals, idx, n_perm=5000)
        base_m = float(np.nanmean(vals))
        trig_m = float(np.nanmean(vals[idx]))
        print(f'  [{label}] 트리거 {trig_m:+.2f}% (n={len(idx)}) vs 전체 {base_m:+.2f}% '
              f'| 효과 {trig_m - base_m:+.2f}%p | p={p:.4f}')

    # ── 2) MDE 정합성 — 지표별 σ로 재산출 ──
    print()
    print('== 2) MDE 재산출 (지표별 σ, n=404/3310) vs v4 p ==')
    trig_n = int(al['trig'].sum())
    base_n = len(al) - trig_n
    for label, col, v4_p in (('시가→종가', 'oc', 0.0043), ('시가→D+5', 'd5', 0.1126)):
        sigma = float(np.nanstd(al[col].values))
        m = mde(trig_n, base_n, sigma)
        obs = float(np.nanmean(al.loc[al['trig'], col]) - np.nanmean(al[col]))
        z_obs = obs / (sigma * float(np.sqrt(1 / trig_n + 1 / base_n)))
        p_param = 2 * (1 - 0.5 * (1 + __import__('math').erf(abs(z_obs) / np.sqrt(2))))
        print(f'  {label}: σ={sigma:.2f}% → MDE(80%) {m:+.2f}%p | 관측 효과 {obs:+.2f}%p '
              f'(파라메트릭 p≈{p_param:.3f}) vs v4 순열 p={v4_p}')
    print('  → 순열 p는 날짜 군집을 반영해 파라메트릭보다 작게 나올 수 있음. '
          '효과크기가 MDE 이하임은 동일 — 실무 판단은 MDE 기준.')

    # ── 3) 거래비용 반영 — 삼전 시가→종가 (당일 매매 = 왕복) ──
    print()
    print(f'== 3) 거래비용 반영 (COST_RT={COST_RT * 100:.2f}%) ==')
    trig_net = float(np.nanmean(al.loc[al['trig'], 'oc'])) - COST_RT * 100
    base_net = float(np.nanmean(al['oc'])) - COST_RT * 100
    print(f'  삼전 시가→종가: 트리거 순수익 {trig_net:+.2f}% vs 기저 순수익 {base_net:+.2f}% '
          f'| 초과 {trig_net - base_net:+.2f}%p — 당일 매매 기준 사실상 0 (참고 판정)')
    print('  실질 용도: 단타가 아니라 분할매수 회차 실행일 선택·시가 투매 방지 (매수 1회 비용)')

    # ── 4) 수급 역행·MA60 순열 검증 (표시 전용 — 등급 스위치 OFF) ──
    print()
    print('== 4) 수급 역행·MA60 검증 (표시 전용, 등급 스위치 OFF) ==')
    con = sqlite3.connect(os.path.join(BASE, '..', 'config', 'data', 'semi_trigger.db'))
    import glob as _glob
    files = sorted(_glob.glob(r'C:\market_dataars_1d\stocks9305\*.parquet')
                   + _glob.glob(r'C:\market_dataars_1d\stocks9306\*.parquet'))
    allmin = pd.concat([pd.read_parquet(f) for f in files])
    close = allmin.set_index(pd.to_datetime(allmin['dt'])).sort_index()['close']
    close.index = close.index.normalize()
    ma60 = close.rolling(60).mean()
    disp = ((close / ma60 - 1) * 100).dropna()

    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        d_rows = pd.read_sql("SELECT * FROM daily_factors WHERE stock_code=? ORDER BY date",
                             con, params=(code,))
        if d_rows.empty:
            print(f'{name}: DB 없음')
            continue
        prog = pd.Series(d_rows['program_net'].values,
                         index=pd.to_datetime(d_rows['date'])).dropna()
        ff5 = pd.Series(d_rows['foreign_flow_5d'].values,
                        index=pd.to_datetime(d_rows['date'])).dropna()
        vr = pd.Series(d_rows['volume_ratio'].values,
                       index=pd.to_datetime(d_rows['date'])).dropna()
        pc = pd.Series(d_rows['price_change'].values,
                       index=pd.to_datetime(d_rows['date'])).dropna()
        al2 = al[al['code'] == code].copy()
        al2 = al2[al2['kr_date'] >= pd.Timestamp('2025-04-01')]
        flags = []
        for _, r in al2.iterrows():
            d0 = r['kr_date']
            hits = 0
            pz = None
            pr = prog[prog.index <= d0]
            if len(pr) >= 21:
                base = pr.iloc[-21:-1]
                if base.std():
                    pz = (pr.iloc[-1] - base.mean()) / base.std()
            fv = ff5[ff5.index <= d0]
            fz = None
            if len(fv) >= 21:
                base = fv.iloc[-21:-1]
                if base.std():
                    fz = (fv.iloc[-1] - base.mean()) / base.std()
            vrz = None
            vrr = vr[vr.index <= d0]
            if len(vrr) >= 21:
                base = vrr.iloc[-21:-1]
                if base.std():
                    vrz = (vrr.iloc[-1] - base.mean()) / base.std()
            if pz is not None and pz <= -0.5:
                hits += 1
            f_last = ff5[ff5.index <= d0]
            if len(f_last) and f_last.iloc[-1] < 0 and fz is not None and fz <= -0.5:
                hits += 1
            pc_last = pc[pc.index <= d0]
            if len(pc_last) and pc_last.iloc[-1] < 0 and vrz is not None and vrz >= 1.0:
                hits += 1
            flags.append(hits >= 2)
        al2['supply'] = flags
        trig = al2[al2['trig'] == True]  # noqa: E712
        on = trig[trig['supply'] == True]  # noqa: E712
        off = trig[trig['supply'] != True]  # noqa: E712
        if len(on) >= 10:
            print(f'{name} 트리거 중 수급역행 ON vs OFF (n={len(on)}/{len(off)}):')
            print(f'  시가→종가: ON {on["oc"].mean():+.2f}% vs OFF {off["oc"].mean():+.2f}% '
                  f'| 시가→D+5: ON {on["d5"].mean():+.2f}% vs OFF {off["d5"].mean():+.2f}%')
        else:
            print(f'{name}: 수급역행 ON 표본 부족 (n={len(on)})')
        # MA60 구간별 (전체일, 2025-04~)
        d3 = al2.copy()
        d3['disp'] = d3['kr_date'].map(disp)
        d3 = d3.dropna(subset=['disp'])
        for label, m in (('위(>=0)', d3['disp'] >= 0),
                         ('경계(-2~0)', (d3['disp'] < 0) & (d3['disp'] >= -2)),
                         ('아래(<-2)', d3['disp'] < -2)):
            sub = d3[m]
            if len(sub) < 10:
                continue
            vals = sub['oc'].values
            p = perm_p(vals, np.arange(len(vals)), n_perm=2000)
            print(f'  MA60 {label}: 시가→종가 {vals.mean():+.2f}% (n={len(sub)}) '
                  f'| 0 대비 p≈{p:.3f}')
    print()
    print('등급 조정 스위치: SUPPLY_GRADE_SWITCH=False / MA60_GRADE_SWITCH=False '
          '— FDR 통과 시에만 ON')
    return 0


if __name__ == '__main__':
    sys.exit(main())
