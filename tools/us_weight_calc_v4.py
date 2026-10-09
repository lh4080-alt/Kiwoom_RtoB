# -*- coding: utf-8 -*-
"""작업 4 검증 v4 — 순열 검정 재설계 + BH-FDR + 구간 분할 + MDE (2026-10-09 Lee 지시).

귀무분포 (v3 결함 수정):
  v3는 '기저일만 5일 블록 재표집'한 평균 분포와 트리거 평균을 비교 → 기저 평균의
  표준오차(≈σ/√3300 ≈ 0.03%p)만 반영해 p가 과소 산출됨.
  v4: 전체 거래일에서 트리거일과 같은 개수를 5일 블록 단위 무작위 추출(10,000회)
  → 그 평균 분포(트리거 측 표본 크기·군집 재현)에서 실제 트리거 평균의 위치로 p.
검정 목록 8건에 BH-FDR 10% 적용. 구간 분할(2012~2019 / 2020~) 재산출.
진입 시점은 검정 대신 검출 가능 최소 효과크기(MDE, 검정력 80%) 보고.
"""
import os
import sys
import warnings
from statistics import NormalDist

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
from modules.semi_trigger.kr_calendar import next_kr_trading_day  # noqa: E402
from modules.semi_trigger.semi_config import STOCK_US_WEIGHTS, SIGMA_WINDOW  # noqa: E402

N_PERM = 10_000
BLOCK = 5
FDR_ALPHA = 0.10
rng = np.random.default_rng(42)


def load_all() -> tuple:
    import yfinance as yf
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
    return us_ret, kr


def build_aligned(us_ret: pd.DataFrame, kr: dict) -> pd.DataFrame:
    rows = []
    for d in us_ret.index:
        e_iso = next_kr_trading_day(d.date().isoformat())
        if not e_iso:
            continue
        e = pd.Timestamp(e_iso)
        for code, h in kr.items():
            idx = h.index
            i = np.where(idx >= e)[0]
            if not len(i) or idx[i[0]] != e or i[0] == 0:
                continue
            j = i[0]
            prev_c = h['Close'].iloc[j - 1]
            rows.append({'us_date': d, 'kr_date': e, 'code': code,
                         'gap': (h['Open'].iloc[j] / prev_c - 1) * 100,
                         'oc': (h['Close'].iloc[j] / h['Open'].iloc[j] - 1) * 100,
                         'full': (h['Close'].iloc[j] / prev_c - 1) * 100,
                         'd5': ((h['Close'].iloc[j + 5] / h['Open'].iloc[j] - 1) * 100
                                if j + 5 < len(h) else np.nan),
                         'MU': us_ret.loc[d, 'MU'] if d in us_ret.index else np.nan,
                         'SNDK': us_ret.loc[d, 'SNDK'] if d in us_ret.index else np.nan})
    return pd.DataFrame(rows).sort_values('us_date').reset_index(drop=True)


def perm_p(all_vals: np.ndarray, trig_idx: np.ndarray,
           n_perm: int = N_PERM, block: int = BLOCK) -> float:
    """순열 검정 — 전체일에서 트리거 크기만큼 5일 블록 무작위 추출 → 평균 분호 위치."""
    n = len(all_vals)
    k = len(trig_idx)
    actual = float(np.nanmean(all_vals[trig_idx]))
    null = []
    for _ in range(n_perm):
        picks = []
        while len(picks) < k:
            b = int(rng.integers(0, max(1, n - block)))
            picks.extend(range(b, b + block))
        picks = np.array(picks[:k])
        null.append(float(np.nanmean(all_vals[picks])))
    null = np.array(null)
    return float((np.sum(null >= actual) + 1) / (n_perm + 1))


def main() -> int:
    us_ret, kr = load_all()
    al = build_aligned(us_ret, kr)

    # 트리거 마스크
    for code, ws in STOCK_US_WEIGHTS.items():
        d = al[al['code'] == code]
        mu = us_ret['MU']
        z_mu = (mu - mu.rolling(SIGMA_WINDOW).mean().shift(1)) / mu.rolling(SIGMA_WINDOW).std().shift(1)
        mask = []
        for _, r in d.iterrows():
            wret = r['MU'] * ws['MU'] + (0 if pd.isna(r['SNDK']) else r['SNDK'] * ws['SNDK'])
            mask.append(bool((z_mu.get(r['us_date'], 0) or 0) <= -1.3 or wret <= -3.5))
        al.loc[d.index, 'trig'] = pd.Series(mask, index=d.index)

    # 잔차 (가중 MU, 60일 롤링 β point-in-time)
    for code, ws in STOCK_US_WEIGHTS.items():
        d = al[al['code'] == code]
        wret = d['MU'] * ws['MU'] + d['SNDK'].fillna(0) * ws['SNDK']
        gaps = d['gap'].values
        n = len(d)
        resid = np.full(n, np.nan)
        for i in range(60, n):
            w_w, w_g = wret.values[i - 60:i], gaps[i - 60:i]
            if np.var(w_w) == 0:
                continue
            beta = np.cov(w_w, w_g)[0, 1] / np.var(w_w)
            resid[i] = gaps[i] - beta * wret.values[i]
        al.loc[d.index, 'resid'] = resid
        al.loc[d.index, 'resid_sig'] = pd.Series(resid, index=d.index).rolling(60).std().shift(1).values

    # ── 검정 8건 (순열) ──
    tests = []

    def add_test(tid, name, d_sub, col, trig_mask, period=''):
        vals = d_sub[col].values
        trig_idx = np.where(trig_mask)[0]
        if len(trig_idx) < 20:
            tests.append({'id': tid, 'name': f'{name} {period}', 'p': None,
                          'trig_mean': np.nan, 'base_mean': np.nan, 'n': len(trig_idx)})
            return
        p = perm_p(vals, trig_idx)
        tests.append({'id': tid, 'name': f'{name} {period}'.strip(), 'p': p,
                      'trig_mean': float(np.nanmean(vals[trig_idx])),
                      'base_mean': float(np.nanmean(vals)),
                      'n': len(trig_idx)})

    for code, name in (('005930', '삼전'), ('000660', '하닉')):
        d = al[al['code'] == code]
        trig = (d['trig'] == True).values  # noqa: E712
        add_test(f'{name} 시가→종가', name, d, 'oc', trig)
        add_test(f'{name} 시가→D+5', name, d, 'd5', trig)
    # 하닉 잔차
    d_h = al[al['code'] == '000660']
    for th in (-1.0, -1.5):
        m = (d_h['resid'] <= th * d_h['resid_sig']).values
        m = np.nan_to_num(m).astype(bool)
        add_test(f'하닉 잔차≤{th}σ 시가→종가', '하닉 잔차', d_h, 'oc', m)
        add_test(f'하닉 잔차≤{th}σ 시가→D+5', '하닉 잔차', d_h, 'd5', m)
    # 삼전 잔차 D+5 (v3 보고분)
    d_s = al[al['code'] == '005930']
    for th in (-1.0, -1.5):
        m = (d_s['resid'] <= th * d_s['resid_sig']).values
        m = np.nan_to_num(m).astype(bool)
        add_test(f'삼전 잔차≤{th}σ 시가→D+5', '삼전 잔차', d_s, 'd5', m)

    # BH-FDR
    valid = [t for t in tests if t['p'] is not None]
    ordered = sorted(valid, key=lambda t: t['p'])
    m = len(ordered)
    cut = 0
    for i, t in enumerate(ordered):
        if t['p'] <= FDR_ALPHA * (i + 1) / m:
            cut = t['p']
    for t in valid:
        t['fdr'] = t['p'] <= cut

    print('== 검정 결과 (순열 10,000회, 5일 블록) ==')
    print(f'{"검정":<26}{"n":>6}{"트리거":>9}{"전체":>9}{"p":>9}  FDR10%')
    print('-' * 72)
    for t in valid:
        mark = '★' if t['fdr'] else ''
        print(f'{t["name"]:<26}{t["n"]:>6}{t["trig_mean"]:>+9.2f}{t["base_mean"]:>+9.2f}'
              f'{t["p"]:>9.4f}  {mark}')
    print(f'BH-FDR 10% 임계 p <= {cut:.4f} — 유의 {sum(1 for t in valid if t["fdr"])}/{m}건')

    # ── 구간 분할 (삼전 D+5, 하닉 잔차 -1.5σ D+5) ──
    print()
    print('== 구간 분할 (2012~2019 / 2020~) ==')
    for tid, code, col, use_resid, th in (
            ('삼전 시가→D+5', '005930', 'd5', False, None),
            ('하닉 잔차≤-1.5σ 시가→D+5', '000660', 'd5', True, -1.5)):
        d = al[al['code'] == code].copy()
        if use_resid:
            d['m'] = (d['resid'] <= th * d['resid_sig']).fillna(False)
        else:
            d['m'] = d['trig'].fillna(False)
        for label, lo, hi in (('2012~2019', '2012-01-01', '2019-12-31'),
                              ('2020~', '2020-01-01', '2026-12-31')):
            sub = d[(d['us_date'] >= lo) & (d['us_date'] <= hi)]
            vals = sub[col].values
            idx = np.where(sub['m'].values)[0]
            if len(idx) < 15:
                print(f'  {tid} [{label}]: n={len(idx)} 표본 부족')
                continue
            p = perm_p(vals, idx, n_perm=5000)
            print(f'  {tid} [{label}]: 트리거 {np.nanmean(vals[idx]):+.2f}% '
                  f'(n={len(idx)}) vs 전체 {np.nanmean(vals):+.2f}% | p={p:.4f}')

    # ── 진입 시점 MDE ──
    print()
    print('== 진입 시점 MDE (검출 가능 최소 효과크기, 검정력 80%, α=0.05 양측) ==')
    zd = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.80)
    for n, sigma in ((65, 2.0), (404, 2.0)):
        mde = zd * sigma * np.sqrt(1 / n + 1 / 3300)
        print(f'n={n} (σ=2% 가정): MDE ≈ {mde * 100:.2f}%p — 이 이하 차이는 검출 불가')

    # ── 하닉 트리거 옵션 안내 ──
    print()
    print('semi_config: HYNIX_TRIGGER_MODE 옵션은 FDR 통과 시 반영 대기')
    return 0


if __name__ == '__main__':
    sys.exit(main())
