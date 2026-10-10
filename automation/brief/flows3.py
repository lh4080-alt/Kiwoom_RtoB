# -*- coding: utf-8 -*-
"""3자 수급 집계·패턴 — Phase 5 사전 선언 v1 (Docs/phase5_수급패턴_사전선언.md) 구현.

원천: config/data/flows3/<code>.parquet (ka10059 확정치, 백만원) — 열 frgn·natfor·orgn·ind·etc_corp·tv
대상: market(KOSPI 주권 합) / block(005930+000660 합) / 005930 / 000660
"""
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'config', 'data')
FLOWS_DIR = os.path.join(DATA, 'flows3')
COLS = ['frgn', 'natfor', 'orgn', 'ind', 'etc_corp', 'tv']
ACTORS = ['frgn', 'orgn', 'ind']
PATTERN_NAMES = {
    '++-': '외인·기관 동반 매수형', '+--': '외인 단독 매수형', '+-+': '외인·개인 매수형',
    '-++': '기관 방어형', '--+': '개인 단독 흡수형', '-+-': '기관 단독 흡수형',
    '+++': '3자 동반 매수형', '---': '3자 동반 매도형',
}
SEMI = ('005930', '000660')


def load_aggregates(codes: list = None) -> dict:
    """{'market','block','005930','000660'} → DataFrame(index=date, cols=COLS) 합계."""
    files = sorted(f for f in os.listdir(FLOWS_DIR) if f.endswith('.parquet'))
    if codes is not None:
        files = [f for f in files if f[:-8] in codes]
    total, per = None, {}
    for f in files:
        df = pd.read_parquet(os.path.join(FLOWS_DIR, f))[COLS]
        code = f[:-8]
        if code in SEMI:
            per[code] = df
        total = df if total is None else total.add(df, fill_value=0)
    out = {'market': total.sort_index()}
    if all(c in per for c in SEMI):
        out['block'] = per[SEMI[0]].add(per[SEMI[1]], fill_value=0).sort_index()
        for c in SEMI:
            out[c] = per[c].sort_index()
    return out


def zprior(s: pd.Series, w: int = 60) -> pd.Series:
    """z = (v − mean(직전 w)) / std(직전 w, ddof=1), 당일 미포함."""
    return (s - s.rolling(w).mean().shift(1)) / s.rolling(w).std().shift(1)


def features(agg: pd.DataFrame) -> pd.DataFrame:
    """일간·20일 누적 비율, z60, 부호 패턴, 강도, 흡수율."""
    f = pd.DataFrame(index=agg.index)
    tv20 = agg['tv'].rolling(20).sum()
    for a in ACTORS + ['etc_corp']:
        f[f'{a}_r'] = agg[a] / agg['tv']
        f[f'{a}_c20'] = agg[a].rolling(20).sum() / tv20
        f[f'{a}_r_z'] = zprior(f[f'{a}_r'])
        f[f'{a}_c20_z'] = zprior(f[f'{a}_c20'])
        f[f'{a}_c20_amt'] = agg[a].rolling(20).sum()
    for kind, suf in (('daily', '_r'), ('cum20', '_c20')):
        f[f'pat_{kind}'] = [
            ''.join('+' if v > 0 else '-' for v in row) if not np.isnan(row).any() else None
            for row in f[[a + suf for a in ACTORS]].values]
        f[f'str_{kind}'] = [
            ''.join('강' if abs(z) >= 1 else '약' for z in row) if not np.isnan(row).any() else None
            for row in f[[a + suf + '_z' for a in ACTORS]].values]
    fr, org = agg['frgn'], agg['orgn']
    f['absorb'] = (org / fr.abs()).where(fr < 0)
    c_fr, c_org = fr.rolling(20).sum(), org.rolling(20).sum()
    f['absorb20'] = (c_org / c_fr.abs()).where(c_fr < 0)
    return f


def pattern_name(p: str) -> str:
    return PATTERN_NAMES.get(p, 'N/A') if p else 'N/A'
