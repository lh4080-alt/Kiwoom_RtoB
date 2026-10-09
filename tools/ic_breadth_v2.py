# -*- coding: utf-8 -*-
"""폭 지표 IC 재검증 — 정의 v2(상장 주권만) vs 기존(ETF·ETN 혼입) 패널 (2026-10-09 Lee 지시).

기존 IC 검증(ic_backtest.py)의 폭 지표는 breadth_panel_full(4,412열, ETF·ETN 1,281개 혼입)로
계산됐다. 같은 패널에서 유니버스만 KRX 상장 주권으로 바꿔 동일 규칙(IC·보정 t·전반/후반·
BH 대상 셀 수 동일)으로 재산출해 나란히 비교한다. 대상: KOSPI t+1/5/10 수익률과 |수익률|.
한계: 유니버스는 현재 상장 주권 — 과거 상장폐지 종목 누락(생존편향). 패널 2021-01~.
"""
import os
import sys
import warnings

import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(ROOT, 'automation'))


def main() -> int:
    from ic_backtest import breadth_indicators, forward_returns, ic_stats, FULL_PANEL_PATH, OHLC_PATH
    from market_breadth import ad_line, pct_above_ma200
    from brief.build import stock_universe

    ohlc = pd.read_parquet(OHLC_PATH)
    targets = forward_returns(ohlc['close'].astype(float))
    panel = pd.read_parquet(FULL_PANEL_PATH)
    univ = stock_universe()
    clean = panel[[c for c in panel.columns if c in univ]]
    print(f'패널 {panel.shape[1]}열 → 상장 주권 {clean.shape[1]}열 '
          f'({panel.index.min().date()}~{panel.index.max().date()})\n')
    old = breadth_indicators(panel, ad_line, pct_above_ma200)
    new = breadth_indicators(clean, ad_line, pct_above_ma200)
    keys = ['r1', 'r5', 'r10', 'a1', 'a5', 'a10']
    print(f'{"지표":<22}{"타깃":>5} | {"기존 IC":>8}{"t":>7}{"판정":>6} | {"v2 IC":>8}{"t":>7}{"판정":>6}')
    print('-' * 80)
    rows = []
    for name in old.columns:
        so, sn = ic_stats(old[name], targets, keys), ic_stats(new[name], targets, keys)
        for k in keys:
            a, b = so.get(k), sn.get(k)
            if not a or not b:
                continue
            mark = lambda s: '★유의' if s['bonf'] else ('○후보' if s['cand'] else '')  # noqa: E731
            print(f'{name:<22}{k:>5} | {a["ic"]:>+8.3f}{a["t"]:>7.2f}{mark(a):>6} | '
                  f'{b["ic"]:>+8.3f}{b["t"]:>7.2f}{mark(b):>6}')
            rows.append({'indicator': name, 'target': k, 'ic_old': a['ic'], 't_old': a['t'],
                         'grade_old': mark(a), 'ic_v2': b['ic'], 't_v2': b['t'], 'grade_v2': mark(b),
                         'h1_v2': b['ic_h1'], 'h2_v2': b['ic_h2']})
    out = os.path.join(ROOT, 'config', 'data', 'verify', 'ic_breadth_v2.csv')
    pd.DataFrame(rows).to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n저장: {out} | 판정 규칙: 후보 |IC|≥0.05·|t|≥2·전반/후반 부호 일치, 유의 |t|≥3.2')
    return 0


if __name__ == '__main__':
    sys.exit(main())
