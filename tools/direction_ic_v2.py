# -*- coding: utf-8 -*-
"""방향 검증 v2 — 중첩 보정 병기(Newey-West) + 사전 고정 극단 구간 4건 + 15건 통합 FDR (2026-10-09).

사전 고정 (결과 확인 전 기록):
  IC 11건      direction_ic.py와 동일 후보·원형 이동 p. 추가로 Newey-West t(lag 20) 병기 —
               rank(y) ~ rank(x) OLS, HAC 표준오차 (20일 선행수익 중첩 보정의 대안 방식)
  극단 4건     E1 외인z20 ≥ +1.5 / E2 외인z20 ≤ −1.5 / E3 고점대비 ≤ −10% / E4 고점대비 ≤ −20%
               통계량 = P(y>0 | 조건) − P(y>0 | 전체), null = 조건 마스크 원형 이동 2,000회(양측)
               연속 구간 수(에피소드) 병기, 전반/후반 차이 부호 일치 요구
  통과         15건 BH-FDR q ≤ 0.10 AND 전반/후반 부호 일치
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
import direction_ic as d1  # noqa: E402


def nw_t(x: np.ndarray, y: np.ndarray, lag: int = 20) -> float:
    import statsmodels.api as sm
    m = ~(np.isnan(x) | np.isnan(y))
    rx = pd.Series(x[m]).rank().values
    ry = pd.Series(y[m]).rank().values
    rx, ry = (rx - rx.mean()) / rx.std(), (ry - ry.mean()) / ry.std()
    fit = sm.OLS(ry, sm.add_constant(rx)).fit(cov_type='HAC', cov_kwds={'maxlags': lag})
    return float(fit.tvalues[1])


def cond_test(mask: np.ndarray, up: np.ndarray, k: int = d1.K_SHIFTS) -> tuple:
    base = up.mean()
    diff = up[mask].mean() - base if mask.sum() else np.nan
    n = len(mask)
    ks = d1.rng.integers(d1.MIN_SHIFT, n - d1.MIN_SHIFT, size=k)
    null = np.array([up[np.roll(mask, s)].mean() - base for s in ks if np.roll(mask, s).sum()])
    p = float((np.sum(np.abs(null) >= abs(diff)) + 1) / (len(null) + 1))
    return diff, p


def episodes(mask: np.ndarray) -> int:
    return int(np.sum(np.diff(np.concatenate([[0], mask.astype(int)])) == 1))


def main() -> int:
    k = pd.read_parquet(os.path.join(d1.D, 'kospi_daily_ohlc.parquet'))['close'].astype(float)
    k.index = [x.date() for x in pd.DatetimeIndex(k.index)]
    y_all = (k.shift(-d1.H) / k - 1) * 100
    cand = d1.load_candidates(k)
    cand['고점대비'] = (k / k.rolling(250).max() - 1).values * 100
    ev = [x for x in k.index if x >= d1.EVAL_START and not np.isnan(y_all[x])]
    y = y_all.reindex(ev).values
    up = (y > 0)
    half = np.array([x >= d1.SPLIT for x in ev])
    rows = []
    for name in [c for c in cand.columns if c != '고점대비']:
        x = cand[name].reindex(ev).values.astype(float)
        ic, p = d1.shift_test(x, y)
        s1 = d1.spearman(x[~half], y[~half])
        s2 = d1.spearman(x[half], y[half])
        rows.append({'검정': f'IC {name}', '효과': ic, 'p_원형이동': p, 'NW_t(lag20)': nw_t(x, y),
                     '전반': s1, '후반': s2, '관측/일수': int((~np.isnan(x)).sum()), '에피소드': None})
    ext = {'E1 외인z20≥+1.5': lambda c: c['외인z20'] >= 1.5,
           'E2 외인z20≤−1.5': lambda c: c['외인z20'] <= -1.5,
           'E3 고점대비≤−10%': lambda c: c['고점대비'] <= -10,
           'E4 고점대비≤−20%': lambda c: c['고점대비'] <= -20}
    for name, fn in ext.items():
        mask = fn(cand).reindex(ev).fillna(False).values.astype(bool)
        diff, p = cond_test(mask, up)
        d_1 = up[~half][mask[~half]].mean() - up[~half].mean() if mask[~half].sum() else np.nan
        d_2 = up[half][mask[half]].mean() - up[half].mean() if mask[half].sum() else np.nan
        rows.append({'검정': name, '효과': diff * 100, 'p_원형이동': p, 'NW_t(lag20)': np.nan,
                     '전반': d_1 * 100, '후반': d_2 * 100, '관측/일수': int(mask.sum()),
                     '에피소드': episodes(mask),
                     '조건부상승': up[mask].mean() * 100 if mask.sum() else np.nan})
    df = pd.DataFrame(rows)
    ps = df['p_원형이동'].dropna().sort_values()
    m, cut = len(ps), 0.0
    for i, (_, pv) in enumerate(ps.items(), 1):
        if pv <= 0.10 * i / m:
            cut = pv
    df['FDR10'] = (df['p_원형이동'] <= cut) & (cut > 0)
    df['부호일치'] = np.sign(df['전반']) == np.sign(df['후반'])
    df['통과'] = df['FDR10'] & df['부호일치']
    print(f'기저 20일 상승확률 {up.mean() * 100:.1f}% (n={len(ev)}) | 통합 검정 {m}건, BH-FDR 10% 임계 p ≤ {cut:.4f}\n')
    print(f'{"검정":<20}{"효과":>8}{"p(원형)":>9}{"NW t":>7}{"전반":>8}{"후반":>8}{"일수":>6}{"에피":>5}  판정')
    print('-' * 88)
    for _, r in df.iterrows():
        nwt = '' if np.isnan(r['NW_t(lag20)']) else f"{r['NW_t(lag20)']:+.2f}"
        ep = '' if r['에피소드'] is None or (isinstance(r['에피소드'], float) and np.isnan(r['에피소드'])) else int(r['에피소드'])
        unit = '%p' if r['검정'].startswith('E') else ''
        extra = f" (조건부 {r['조건부상승']:.0f}%)" if r['검정'].startswith('E') else ''
        print(f"{r['검정']:<20}{r['효과']:>+7.3f}{unit:<1}{r['p_원형이동']:>8.4f}{nwt:>7}{r['전반']:>+8.2f}"
              f"{r['후반']:>+8.2f}{r['관측/일수']:>6}{str(ep):>5}  {'통과' if r['통과'] else '미통과'}{extra}")
    df.to_csv(os.path.join(d1.D, 'verify', 'direction_ic_v2.csv'), index=False, encoding='utf-8-sig')
    return 0


if __name__ == '__main__':
    sys.exit(main())
