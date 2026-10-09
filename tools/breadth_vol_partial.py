# -*- coding: utf-8 -*-
"""폭 → 변동성 신호의 독립성 — 실현변동성 통제 후 잔존 여부 (2026-10-09 Lee 지시).

사전 고정: 타깃 a_h = |KOSPI h일 선행수익률| (h = 1, 5). 순위 회귀
  rank(a_h) ~ rank(폭) [+ rank(직전 20일 실현변동성) + rank(직전 |일간수익률|)]
HAC 표준오차 (maxlags = max(h, 5)). 통제 전·후 폭 계수 t 비교 — 통제 후 |t| ≥ 3.2면 독립 정보로 확정,
아니면 변동성 줄은 실현변동성 하나로 충분.
폭 = k81 장기 패널(상장 주권, 2014-07~): A/D 20일 변화, 200일선 위 비율, 200일선 비율 10일 변화.
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(ROOT, 'automation'))


def rz(s: pd.Series) -> pd.Series:
    r = s.rank()
    return (r - r.mean()) / r.std()


def main() -> int:
    import statsmodels.api as sm
    from ic_backtest import breadth_indicators, OHLC_PATH
    from market_breadth import ad_line, pct_above_ma200
    from brief.build import stock_universe
    k = pd.read_parquet(OHLC_PATH)['close'].astype(float)
    r = k.pct_change() * 100
    controls = pd.DataFrame({'rv20': r.rolling(20).std(), 'abs_r': r.abs()})
    pnl = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'breadth_k81_panel.parquet'))
    univ = stock_universe()
    clean = pnl[[c for c in pnl.columns if c in univ]]
    br = breadth_indicators(clean, ad_line, pct_above_ma200)
    # 브리프 표시 지표 — A/D 20일 비율(상승÷하락, 유니버스 크기 무관)
    chg = clean.pct_change(fill_method=None)
    up, dn = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    br['ad_ratio20'] = up.rolling(20).sum() / dn.rolling(20).sum().replace(0, np.nan)
    br.index = pd.DatetimeIndex(br.index)
    print(f'{"폭 지표":<22}{"타깃":>5}{"n":>6} | {"통제 전 t":>9} | {"통제 후 t":>9}{"rv20 t":>8}{"|r| t":>7}  판정')
    print('-' * 86)
    rows = []
    for h in (1, 5):
        a = ((k.shift(-h) / k - 1).abs() * 100).rename('a')
        for name in br.columns:
            df = pd.concat([a, br[name].rename('x'), controls], axis=1).dropna()
            df = df[df.index >= '2014-07-01']
            Y = rz(df['a'])
            lag = max(h, 5)
            f0 = sm.OLS(Y, sm.add_constant(rz(df['x']))).fit(cov_type='HAC', cov_kwds={'maxlags': lag})
            X = pd.concat([rz(df['x']), rz(df['rv20']), rz(df['abs_r'])], axis=1)
            f1 = sm.OLS(Y, sm.add_constant(X)).fit(cov_type='HAC', cov_kwds={'maxlags': lag})
            t0, t1 = float(f0.tvalues.iloc[1]), float(f1.tvalues.iloc[1])
            verdict = '독립 정보(유지)' if abs(t1) >= 3.2 else ('약화(후보)' if abs(t1) >= 2 else '소멸')
            print(f'{name:<22}{"a" + str(h):>5}{len(df):>6} | {t0:>+9.2f} | {t1:>+9.2f}'
                  f'{float(f1.tvalues.iloc[2]):>+8.2f}{float(f1.tvalues.iloc[3]):>+7.2f}  {verdict}')
            rows.append({'indicator': name, 'target': f'a{h}', 'n': len(df), 't_raw': t0,
                         't_controlled': t1, 't_rv20': float(f1.tvalues.iloc[2]),
                         't_absr': float(f1.tvalues.iloc[3]), 'verdict': verdict})
    pd.DataFrame(rows).to_csv(os.path.join(ROOT, 'config', 'data', 'verify', 'breadth_vol_partial.csv'),
                              index=False, encoding='utf-8-sig')
    return 0


if __name__ == '__main__':
    sys.exit(main())
