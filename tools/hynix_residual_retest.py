# -*- coding: utf-8 -*-
"""하닉 잔차 갭 검정 재실행 — 수정 β (ddof 일관) 단일 검정 (2026-10-09 Lee 지시).

v4 결과: 하닉 잔차 ≤ −1.5σ 시가→D+5 p=0.0448 (n=176) — FDR 10% 미달, 경계.
기존 β = np.cov(ddof=1)/np.var(ddof=0) → n/(n−1)배 과대 (V2 확인). 여기서는
β = Cov/Var 모두 ddof=1, 창 i−60..i−1 (brief.calc.rolling_beta와 동일)로 잔차를 다시 계산.
검정·순열 틀은 v4 그대로 (5일 블록, 10,000회, 전체일에서 트리거 크기만큼 추출).
"""
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
from us_weight_calc_v4 import build_aligned, load_all, perm_p  # noqa: E402
from modules.semi_trigger.semi_config import STOCK_US_WEIGHTS  # noqa: E402


def residuals(d: pd.DataFrame, ws: dict, fixed_ddof: bool) -> tuple:
    wret = (d['MU'] * ws['MU'] + d['SNDK'].fillna(0) * ws['SNDK']).values
    gaps = d['gap'].values
    n = len(d)
    resid = np.full(n, np.nan)
    for i in range(60, n):
        x, y = wret[i - 60:i], gaps[i - 60:i]
        var = np.var(x, ddof=1) if fixed_ddof else np.var(x)
        if var == 0:
            continue
        resid[i] = gaps[i] - (np.cov(x, y)[0, 1] / var) * wret[i]
    sig = pd.Series(resid).rolling(60).std().shift(1).values
    return resid, sig


def main() -> int:
    us_ret, kr = load_all()
    al = build_aligned(us_ret, kr)
    d = al[al['code'] == '000660'].reset_index(drop=True)
    ws = STOCK_US_WEIGHTS['000660']
    vals = d['d5'].values
    print(f'{"β 산식":<26}{"n":>5}{"트리거 D+5":>12}{"전체":>8}{"p":>9}')
    for label, fixed in (('기존 (ddof 불일치)', False), ('수정 (ddof=1 일관)', True)):
        resid, sig = residuals(d, ws, fixed)
        m = np.nan_to_num(resid <= -1.5 * sig).astype(bool)
        idx = np.where(m)[0]
        p = perm_p(vals, idx)
        print(f'{label:<26}{len(idx):>5}{np.nanmean(vals[idx]):>+11.2f}%'
              f'{np.nanmean(vals):>+7.2f}%{p:>9.4f}')
    print('\n판정 기준: v4 BH-FDR 10% 임계 p ≤ 0.0043 (10건 중 1건) — 단일 재검정이라 임계 불변')
    return 0


if __name__ == '__main__':
    sys.exit(main())
