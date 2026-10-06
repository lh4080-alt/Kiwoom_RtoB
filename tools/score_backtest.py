# -*- coding: utf-8 -*-
"""Task D — 전 지표 유지 방향 점수 3안 walk-forward 비교 + 변동성 레짐 점수 (2026-10-06).

Lee 설계 반영:
  - horizon만큼 purge (학습 말단과 평가 시작 사이 gap = 최대 horizon)
  - 기준선: 항상 상승 / 5일 모멘텀 (AUC·Brier·적중률 + bootstrap 95% CI)
  - 점수 5분위 → 이후 1/5/20일 수익률
  - 출력 2축: ① 방향 점수 (3안: 균등 / IC 수축 / L2 로지스틱)
             ② 변동성 레짐 점수 (증분 검증 생존 지표 + ATR)
  - 원칙: 모든 지표 파이프라인 유지 (자동 제외 금지), 통과 지표는 플래그만
  - look-ahead 방지: 특징은 252일 롤링 z, 학습은 평가창 이전 + purge

실행: beelink에서 python tools/score_backtest.py
"""
import asyncio
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))

OHLC_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
FLOWS_PATH = os.path.join(BASE, '..', 'config', 'data', 'investor_flows.parquet')
FULL_PANEL_PATH = os.path.join(BASE, '..', 'config', 'data', 'breadth_panel_full.parquet')

Z_WIN = 252          # 롤링 z 창 (look-ahead 방지)
TRAIN_Y, TEST_Y, PURGE = 3, 1, 10  # 학습 3년 / 평가 1년 / purge 10거래일
H_MAIN = 5           # 주 타깃 horizon
N_BOOT = 1000
SEED = 42


# ── 데이터/특징 ──────────────────────────────────────────────
def build_features() -> pd.DataFrame:
    from market_breadth import ad_line as ad_line_fn, adx_atr as adx_atr_fn, pct_above_ma200 as pct_above_fn
    from ic_backtest import (breadth_indicators, di_columns, fetch_kospi_ohlc_600,
                             flow_indicators, index_indicators)

    async def gather():
        ohlc = await fetch_kospi_ohlc_600()
        return ohlc

    ohlc = asyncio.get_event_loop().run_until_complete(gather())
    feats = pd.DataFrame(index=ohlc.index)
    idx_ind = index_indicators(ohlc, adx_atr_fn)
    for c in idx_ind.columns:
        feats[c] = idx_ind[c]
    if os.path.exists(FULL_PANEL_PATH):
        panel = pd.read_parquet(FULL_PANEL_PATH)
        b = breadth_indicators(panel, ad_line_fn, pct_above_fn)
        for c in b.columns:
            feats[f'b_{c}'] = b[c]
    if os.path.exists(FLOWS_PATH):
        flows = pd.read_parquet(FLOWS_PATH)
        flows.index = pd.to_datetime(flows.index, format='%Y%m%d')
        f = flow_indicators(flows)
        for c in f.columns:
            feats[f'f_{c}'] = f[c]
    try:
        from ic_backtest import macro_indicators_15y
        m = asyncio.get_event_loop().run_until_complete(macro_indicators_15y())
        for c in m.columns:
            feats[f'm_{c}'] = m[c]
    except Exception:
        pass
    # look-ahead 방지: 252일 롤링 z
    return feats.rolling(Z_WIN, min_periods=120).apply(
        lambda w: (w.iloc[-1] - np.nanmean(w)) / (np.nanstd(w) or 1.0), raw=False), ohlc


def auc_score(y: np.ndarray, s: np.ndarray) -> float:
    """Mann-Whitney AUC."""
    pos, neg = s[y == 1], s[y == 0]
    if not len(pos) or not len(neg):
        return 0.5
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    # tie 처리 간략 (동순위 평균 미적용 — 점수 연속이라 영향 미미)
    rp = ranks[:len(pos)].sum()
    return (rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float = 1.0,
                 lr: float = 0.5, iters: int = 800) -> np.ndarray:
    """L2 로지스틱 (절편 비규제). 람다는 사전 선언 고정 — 튜닝 없음."""
    beta = np.zeros(X.shape[1] + 1)
    Xb = np.c_[np.ones(len(X)), X]
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ beta, -30, 30)))
        grad = Xb.T @ (p - y) / len(y)
        grad[1:] += lam * beta[1:]
        beta -= lr * grad
    return beta


def main() -> int:
    from ic_backtest import di_columns

    feats, ohlc = build_features()
    c = ohlc['close']
    r = {h: (c.shift(-h) / c - 1) * 100 for h in (1, 5, 20)}
    up5 = (r[5] > 0).astype(float)
    a5 = r[5].abs()

    cols = list(feats.columns)
    print(f'[score] 특징 {len(cols)}개: {cols}')

    # walk-forward 구간
    dates = feats.index
    years = dates.year.unique()
    test_years = [y for y in years if y >= years[0] + TRAIN_Y + 1]
    oos = {k: pd.Series(np.nan, index=dates) for k in ('eq', 'icsh', 'logit', 'mom')}
    start = datetime.now()

    for ty in test_years:
        t0 = dates[dates.year == ty][0]
        t1 = dates[dates.year == ty][-1]
        train_end = t0 - pd.Timedelta(days=1)
        eval_end = t1
        tr_mask = (dates <= train_end - pd.Timedelta(days=PURGE)) & \
                  (dates >= t0 - pd.Timedelta(days=365 * (TRAIN_Y + 1)))
        te_mask = (dates >= t0) & (dates <= eval_end)
        Xtr, Xte = feats[tr_mask], feats[te_mask]
        ytr = up5[tr_mask]
        valid_tr = Xtr.notna().all(axis=1) & ytr.notna()
        Xtr, ytr = Xtr[valid_tr].fillna(0), ytr[valid_tr]

        # A) 균등 — 모든 지표 z 합 평균 (결측 지표 제외 평균)
        oos['eq'][te_mask] = Xte.fillna(0).mean(axis=1)

        # B) IC 수축 — 학습창 Spearman IC, soft-threshold 0.05
        ic = Xtr.apply(lambda s: s.corr(up5[valid_tr], method='spearman'))
        w = np.sign(ic) * (ic.abs() - 0.05).clip(lower=0)
        if w.abs().sum() > 0:
            w = w / w.abs().sum()
            oos['icsh'][te_mask] = Xte.fillna(0) @ w.reindex(Xte.columns).fillna(0)

        # C) L2 로지스틱 — 결측은 열 평균(학습창) 대체
        colmean = Xtr.mean()
        Xtr_f = Xtr.fillna(colmean).values
        Xte_f = Xte.fillna(colmean).values
        beta = fit_logistic(Xtr_f, ytr.values)
        oos['logit'][te_mask] = pd.Series(1 / (1 + np.exp(-np.c_[np.ones(len(Xte_f)),
                                                                 Xte_f] @ beta)), index=Xte.index)

        # 기준선: 5일 모멘텀 부호
        oos['mom'][te_mask] = feats.get('ret5', pd.Series(0, index=dates))[te_mask]

    # ── 평가 ─────────────────────────────────────────────────
    ev = pd.DataFrame({k: oos[k] for k in oos})
    ev['up5'] = up5
    ev['a5'] = a5
    ev['r1'], ev['r5'], ev['r20'] = r[1], r[5], r[20]
    ev = ev.dropna(subset=['up5'])
    print(f'[score] OOS 표본: {len(ev)}일 ({ev.index.min().date()}~{ev.index.max().date()}) '
          f'({(datetime.now()-start).total_seconds():.0f}초)')

    rng = np.random.default_rng(SEED)

    def ci(x):
        x = x[~np.isnan(x)]
        boots = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(N_BOOT)]
        return np.percentile(boots, [2.5, 97.5])

    base_up = ev['up5'].mean()
    print()
    print(f'== ① 방향 점수 (OOS {len(ev)}일, 타깃: 5일 상승) ==')
    print(f'{"모델":<10}{"AUC":>7}{"Brier":>8}{"적중률":>8}  적중률 95%CI      비고')
    print('-' * 70)
    print(f'{"항상상승":<10}{0.5:>7.3f}{(1-base_up):>8.3f}{base_up:>8.3f}  —            기준선')
    for k, label in (('mom', '모멘텀(기준)'), ('eq', 'A 균등'), ('icsh', 'B IC수축'), ('logit', 'C 로지스틱')):
        s = ev[k].values
        y = ev['up5'].values
        pred = (s > np.nanmedian(s)).astype(float) if k != 'logit' else (s > 0.5).astype(float)
        acc = np.nanmean((pred == y).astype(float))
        brier = np.nanmean((s - y) ** 2)
        lo, hi = ci((pred == y).astype(float))
        note = '기준선' if k == 'mom' else ''
        print(f'{label:<10}{auc_score(y[~np.isnan(s)], s[~np.isnan(s)]):>7.3f}'
              f'{brier:>8.3f}{acc:>8.3f}  [{lo:.3f},{hi:.3f}]  {note}')

    print()
    print('== 점수 5분위 → 이후 수익률 (B IC수축 기준) ==')
    q = pd.qcut(ev['icsh'], 5, labels=False, duplicates='drop')
    for qi in sorted(ev.groupby(q).groups.keys()):
        g = ev[q == qi]
        print(f'Q{qi + 1}: n={len(g):>4} | r1 {g["r1"].mean():+.2f}% | r5 {g["r5"].mean():+.2f}% '
              f'| r20 {g["r20"].mean():+.2f}% | 승률(5일) {(g["r5"] > 0).mean() * 100:.0f}%')

    # ── ② 변동성 레짐 점수 ───────────────────────────────────
    print()
    print('== ② 변동성 레짐 점수 (a5 예측, 등가중 z — 증분 생존 지표 우선) ==')
    vol_cols = [x for x in feats.columns if x in
                ('atr_pctile60', 'adx14', 'f_orgn_cum20', 'm_usd_chg5', 'f_frgnr_minus_orgn')]
    vs = feats[vol_cols].mean(axis=1)
    ev2 = pd.DataFrame({'vs': vs, 'a5': a5, 'r5': r[5]}).dropna()
    ic_v = ev2['vs'].corr(ev2['a5'], method='spearman')
    print(f'구성: {vol_cols}')
    print(f'변동성 점수 vs |r5| IC: {ic_v:+.3f} (n={len(ev2)})')
    qv = pd.qcut(ev2['vs'], 5, labels=False, duplicates='drop')
    for qi in sorted(ev2.groupby(qv).groups.keys()):
        g = ev2[qv == qi]
        print(f'Q{qi + 1}: |r5| 평균 {g["a5"].mean():.2f}% | r5 {g["r5"].mean():+.2f}% (n={len(g)})')

    # ── 결합 문구 예시 (현재 값) ─────────────────────────────
    print()
    print('== 결합 알림 문구 (현재 시점) ==')
    cur_adx = feats['adx14'].iloc[-1] if 'adx14' in feats else np.nan
    cur_dn = feats['adx_dn'].iloc[-1] if 'adx_dn' in feats else np.nan
    cur_vs = vs.iloc[-1]
    print(f'ADX={cur_adx:.0f}, 하락추세중ADX={cur_dn if not np.isnan(cur_dn) else "N/A"}, '
          f'변동성점수 z={cur_vs:+.2f}')
    hi_vol = cur_vs > 0.5
    dn_strong = (not np.isnan(cur_dn)) and cur_dn > 0.5
    if hi_vol and dn_strong:
        print('→ 문구: "고변동 + 하락추세 강함 → 반등 가능성 (평균회귀 관찰 기반, 표시 전용)"')
    elif hi_vol:
        print('→ 문구: "고변동 레짐 — 큰 움직임 가능성"')
    else:
        print('→ 문구: "변동성·추세 모두 약함 — 정보 없음"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
