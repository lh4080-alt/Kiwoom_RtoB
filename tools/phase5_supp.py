# -*- coding: utf-8 -*-
"""Phase 5 합격 셀 보조 점검 (참고 — 사전선언 합격 판정을 바꾸지 않음, 2026-10-10).

1) 해상도: 원형 이동 null 20,000회 (본 실행 2,000회는 p 하한 0.0005에 5셀이 모두 걸림)
2) 증분성: y = b0 + b1·조건 + 통제, Newey-West HAC (lag = h)
   통제 = 직전 20일 실현변동성(일간 수익률 표준편차) · 직전 5일 |수익률| · (방향 타깃이면) 직전 20일 수익률
   → 조건이 변동성 군집·모멘텀의 대리인지 확인
출력: config/data/verify/phase5/supp_cells.csv
"""
import os
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
import phase5_flows as P  # noqa: E402
from brief.flows3 import cluster_labels, features, load_aggregates  # noqa: E402

CELLS = [('market', 'cum20', '-++', 'a', 5), ('block', 'cum20', '-++', 'a', 5),
         ('000660', 'cum20', '++-', 'r', 5), ('000660', 'cum20', '-++', 'a', 5),
         ('market', 'cluster', 1, 'r', 20)]
K = 20000


def main() -> int:
    rng = np.random.default_rng(20261011)
    aggs = load_aggregates()
    feats = {u: features(aggs[u][aggs[u].index >= P.EVAL_START - pd.Timedelta(days=200)]) for u in
             ('market', 'block', '000660')}
    feats = {u: f[f.index >= P.EVAL_START] for u, f in feats.items()}
    tg = P.target_index()
    rows = []
    for univ, kind, code, tk, h in CELLS:
        f = feats[univ]
        idx = f.index
        mask = ((cluster_labels(f) == code).values if kind == 'cluster'
                else np.array([p == code for p in f[f'pat_{kind}'].values]))
        r = P.fwd(tg[univ], h).reindex(idx)
        y = r.abs() if tk == 'a' else r
        d1 = tg[univ].pct_change() * 100
        X = pd.DataFrame({'cond': mask.astype(float),
                          'rv20': d1.rolling(20).std().reindex(idx),
                          'a5_past': (tg[univ] / tg[univ].shift(5) - 1).abs().reindex(idx) * 100}, index=idx)
        if tk == 'r':
            X['r20_past'] = ((tg[univ] / tg[univ].shift(20) - 1) * 100).reindex(idx)
        df = pd.concat([y.rename('y'), X], axis=1).dropna()
        # 1) 해상도
        yy, mm = df['y'].values, df['cond'].values.astype(bool)
        base, obs = yy.mean(), yy[mm].mean() - yy.mean()
        ks = rng.integers(P.MIN_SHIFT, len(yy) - P.MIN_SHIFT, size=K)
        null = np.array([yy[np.roll(mm, s)].mean() - base for s in ks])
        p_hi = (np.sum(np.abs(null) >= abs(obs)) + 1) / (K + 1)
        # 2) 증분성
        fit0 = sm.OLS(df['y'], sm.add_constant(df[['cond']])).fit(cov_type='HAC', cov_kwds={'maxlags': h})
        fit1 = sm.OLS(df['y'], sm.add_constant(df.drop(columns='y'))).fit(cov_type='HAC',
                                                                          cov_kwds={'maxlags': h})
        rows.append({'univ': univ, 'cond': f'{kind} {code}', 'target': f'{tk}{h}', 'n_days': int(mm.sum()),
                     'diff': obs, 'p_20000': p_hi,
                     'b_raw': fit0.params['cond'], 't_raw_NW': fit0.tvalues['cond'],
                     'b_ctrl': fit1.params['cond'], 't_ctrl_NW': fit1.tvalues['cond'],
                     'shrink_pct': (1 - fit1.params['cond'] / fit0.params['cond']) * 100})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(P.OUT, 'supp_cells.csv'), index=False, encoding='utf-8-sig')
    print(out.round(4).to_string())
    return 0


if __name__ == '__main__':
    sys.exit(main())
