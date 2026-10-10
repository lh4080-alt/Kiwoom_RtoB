# -*- coding: utf-8 -*-
"""Phase 5 — 3자 수급 패턴 검증 (사전 선언 v1: Docs/phase5_수급패턴_사전선언.md, ff91b55).

실행: python tools/phase5_flows.py
출력: config/data/verify/phase5/{cells,quintiles,exhaustion_events,earnings_events,cluster_eta}.csv,
      phase5_report.md, config/data/verify/cell_registry.json(누적 셀 수)
"""
import json
import os
import sys
import warnings
from datetime import time as dtime, timedelta

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
from brief.flows3 import (ACTORS, PATTERN_NAMES, cluster_labels, exhaustion_signals,  # noqa: E402
                          external_frame, features, load_aggregates, resid_expl, zprior)

D = os.path.join(ROOT, 'config', 'data')
OUT = os.path.join(D, 'verify', 'phase5')
EVAL_START = pd.Timestamp('2014-07-01')
SPLIT = pd.Timestamp('2020-01-01')
K_NULL, K_BOOT, MIN_SHIFT = 2000, 2000, 60
if '--smoke' in sys.argv:          # 코드 점검용 — 결과로 쓰지 않음
    K_NULL, K_BOOT = 30, 30
    OUT = os.path.join(D, 'verify', 'phase5_smoke')
rng = np.random.default_rng(20261010)
CELLS = []


# ── 타깃 ──────────────────────────────────────────────────────
def target_index() -> dict:
    k = pd.read_parquet(os.path.join(D, 'kospi_daily_ohlc.parquet'))['close'].astype(float)
    k.index = pd.DatetimeIndex(k.index)
    p = pd.read_parquet(os.path.join(D, 'breadth_k81_panel.parquet'))
    p.index = pd.DatetimeIndex(p.index)
    out = {'market': k}
    for c in ('005930', '000660'):
        out[c] = p[c].dropna()
    r = pd.concat([out['005930'].pct_change(), out['000660'].pct_change()], axis=1).mean(axis=1)
    out['block'] = (1 + r.fillna(0)).cumprod()
    return out


def fwd(idx: pd.Series, h: int) -> pd.Series:
    return (idx.shift(-h) / idx - 1) * 100


# ── 공통 검정 ─────────────────────────────────────────────────
def episodes_of(mask: np.ndarray) -> list:
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        if not m and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def cond_cell(study, univ, cond, target, mask, y, dates, h):
    """조건부 평균 − 전체 평균, 원형 이동 p, 블록 부트 CI, 구간 부호, 상위 2 에피소드 제외."""
    ok = ~np.isnan(y)
    mask, y, dates = mask[ok], y[ok], dates[ok]
    n = int(mask.sum())
    base = float(y.mean())
    rec = {'study': study, 'univ': univ, 'cond': cond, 'target': target, 'n': n}
    eps = episodes_of(mask)
    rec['episodes'] = len(eps)
    if n == 0:
        CELLS.append({**rec, 'p': np.nan})
        return rec
    cm = float(y[mask].mean())
    rec.update({'cond_mean': cm, 'base_mean': base, 'diff': cm - base,
                'median': float(np.median(y[mask])), 'down_prob': float((y[mask] < 0).mean() * 100),
                'base_down': float((y < 0).mean() * 100)})
    N = len(y)
    ks = rng.integers(MIN_SHIFT, N - MIN_SHIFT, size=K_NULL)
    null = np.array([y[np.roll(mask, s)].mean() - base for s in ks])
    rec['p'] = float((np.sum(np.abs(null) >= abs(cm - base)) + 1) / (K_NULL + 1))
    bl = max(h, 5)
    boots = []
    for _ in range(K_BOOT):
        st = rng.integers(0, N - bl, size=N // bl + 1)
        ix = np.concatenate([np.arange(s, s + bl) for s in st])[:N]
        mm = mask[ix]
        if mm.any():
            boots.append(y[ix][mm].mean())
    rec['ci_lo'], rec['ci_hi'] = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))
    h1 = dates < SPLIT
    for lbl, sel in (('d_h1', h1), ('d_h2', ~h1)):
        rec[lbl] = (float(y[sel & mask].mean() - y[sel].mean())
                    if (sel & mask).any() else np.nan)
    contrib = sorted(eps, key=lambda e: -abs((y[e[0]:e[1]] - base).sum()))
    keep = mask.copy()
    for a, b in contrib[:2]:
        keep[a:b] = False
    rec['d_ex_top2'] = float(y[keep].mean() - base) if keep.any() else np.nan
    yrs = pd.Series([dates[a].year for a, _ in eps]).value_counts().sort_index()
    rec['years'] = ','.join(f'{y_ % 100:02d}:{c}' for y_, c in yrs.items())
    CELLS.append(rec)
    return rec


def ic_cell(study, univ, cond, target, x, y, dates):
    ok = ~(np.isnan(x) | np.isnan(y))
    rec = {'study': study, 'univ': univ, 'cond': cond, 'target': target, 'n': int(ok.sum()),
           'episodes': np.nan}
    if ok.sum() < 100:
        CELLS.append({**rec, 'p': np.nan})
        return rec
    # 원형 이동은 전체 시계열(NaN 포함)에서 x를 이동 → 정의 구간의 자기상관 보존
    def ic(a, b):
        m = ~(np.isnan(a) | np.isnan(b))
        return float(np.corrcoef(pd.Series(a[m]).rank(), pd.Series(b[m]).rank())[0, 1])
    val = ic(x, y)
    N = len(x)
    ks = rng.integers(MIN_SHIFT, N - MIN_SHIFT, size=K_NULL)
    null = np.array([ic(np.roll(x, s), y) for s in ks])
    h1 = dates < SPLIT
    rec.update({'diff': val, 'p': float((np.sum(np.abs(null) >= abs(val)) + 1) / (K_NULL + 1)),
                'd_h1': ic(x[h1], y[h1]), 'd_h2': ic(x[~h1], y[~h1]), 'd_ex_top2': val})
    CELLS.append(rec)
    return rec


# ── 분석 ──────────────────────────────────────────────────────
def run_patterns(feats, targets):
    for univ, f in feats.items():
        y_idx = targets[univ]
        dates = f.index.values
        for kind in ('daily', 'cum20'):
            pat = f[f'pat_{kind}'].values
            for code in PATTERN_NAMES:
                mask = np.array([p == code for p in pat])
                for h in (5, 20):
                    r = fwd(y_idx, h).reindex(f.index).values
                    cond_cell('2-1', univ, f'{kind} {code} {PATTERN_NAMES[code]}', f'r{h}', mask, r,
                              pd.DatetimeIndex(dates), h)
                    cond_cell('2-1', univ, f'{kind} {code} {PATTERN_NAMES[code]}', f'a{h}', mask,
                              np.abs(r), pd.DatetimeIndex(dates), h)


def run_absorption(feats, targets):
    qrows = []
    for univ, f in feats.items():
        dates = pd.DatetimeIndex(f.index)
        for col in ('absorb', 'absorb20'):
            x = f[col].values.astype(float)
            for h in (5, 20):
                r = fwd(targets[univ], h).reindex(f.index).values
                ic_cell('2-2', univ, col, f'r{h}', x, r, dates)
                ic_cell('2-2', univ, col, f'a{h}', x, np.abs(r), dates)
                # 5분위 — 분위 경계는 그 시점까지 확장 창 (point-in-time), 최소 250 관측
                s = pd.Series(x, index=f.index)
                q = pd.Series(np.nan, index=f.index)
                vals = []
                for i, (d, v) in enumerate(s.items()):
                    if np.isnan(v):
                        continue
                    if len(vals) >= 250:
                        q.iloc[i] = int(np.searchsorted(np.percentile(vals, [20, 40, 60, 80]), v)) + 1
                    vals.append(v)
                for qi in range(1, 6):
                    m = (q.values == qi)
                    rr = r[m & ~np.isnan(r)]
                    if len(rr):
                        qrows.append({'univ': univ, 'measure': col, 'h': h, 'quintile': qi, 'n': len(rr),
                                      'episodes': len(episodes_of(m)), 'mean_r': rr.mean(),
                                      'mean_abs_r': np.abs(rr).mean(), 'down_prob': (rr < 0).mean() * 100})
    pd.DataFrame(qrows).to_csv(os.path.join(OUT, 'quintiles.csv'), index=False, encoding='utf-8-sig')


def run_exhaustion(feats, targets):
    rows = []
    for univ in ('market', 'block'):
        f = feats[univ]
        z = f['frgn_c20_z'].values
        net = f['frgn_r'].values
        idx = f.index
        sig = exhaustion_signals(f)
        for j in np.where(sig)[0]:
            rows.append({'univ': univ, 'signal': idx[j].date(),
                         **{f'r{h}': fwd(targets[univ], h).get(idx[j], np.nan) for h in (5, 20, 60)}})
        for h in (5, 20, 60):
            r = fwd(targets[univ], h).reindex(idx).values
            cond_cell('2-6', univ, '외인 매도 종료(z≤−2 후 3일 연속 순매수)', f'r{h}', sig, r, idx, h)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'exhaustion_events.csv'), index=False, encoding='utf-8-sig')


def run_external(feats, targets):
    out = {}
    for univ in ('market', 'block'):
        f = feats[univ]
        idx = f.index
        X = external_frame(idx, targets['market'])
        resid, fit_hat = resid_expl(f['frgn_r'] * 100, X)
        out[univ] = {'resid': resid, 'expl': fit_hat}
        rz, ez = zprior(resid), zprior(fit_hat)
        for cond, m in (('잔차 z≤−1 (설명 안 되는 매도)', (rz <= -1).values),
                        ('설명분 z≤−1 (외부요인 매도)', (ez <= -1).values)):
            for h in (5, 20):
                r = fwd(targets[univ], h).reindex(idx).values
                cond_cell('2-4', univ, cond, f'r{h}', m, r, idx, h)
                cond_cell('2-4', univ, cond, f'a{h}', m, np.abs(r), idx, h)
    # 라벨 비교 — 현재 ③괴리有 vs 잔차 기반, 타깃 KOSPI
    fb = feats['block']
    idx = fb.index
    px5 = targets['market'].pct_change(5).reindex(idx) * 100
    cur = ((px5 > 0) & (fb['frgn_c20_amt'] < 0)).values
    res20 = out['block']['resid'].rolling(20).sum()
    alt = ((px5 > 0) & (res20 < 0)).values
    for cond, m in (('현재 ③괴리有 (px5>0 & 블록외인20일<0)', cur),
                    ('잔차 괴리有 (px5>0 & 블록외인잔차20일<0)', alt)):
        for h in (5, 20):
            r = fwd(targets['market'], h).reindex(idx).values
            cond_cell('2-4L', 'market', cond, f'r{h}', m, r, idx, h)
            cond_cell('2-4L', 'market', cond, f'a{h}', m, np.abs(r), idx, h)
    return out


def run_cluster(feats, targets):
    eta_rows = []
    for univ in ('market', 'block'):
        f = feats[univ]
        lab = cluster_labels(f)
        idx = f.index
        for c in (1, 2, 3, 4):
            m = (lab == c).values
            for h in (5, 20):
                r = fwd(targets[univ], h).reindex(idx).values
                cond_cell('2-3', univ, f'군집 C{c}', f'r{h}', m, r, idx, h)
                cond_cell('2-3', univ, f'군집 C{c}', f'a{h}', m, np.abs(r), idx, h)
        # η² 비교 (같은 날짜): 군집 라벨 vs 2-1 누적 패턴, 타깃 r20
        r20 = fwd(targets[univ], 20).reindex(idx)
        dfc = pd.DataFrame({'lab': lab, 'pat': f['pat_cum20'], 'y': r20}).dropna()

        def eta(g):
            grand = dfc['y'].mean()
            ss_b = sum(len(v) * (v.mean() - grand) ** 2 for _, v in dfc.groupby(g)['y'])
            return ss_b / ((dfc['y'] - grand) ** 2).sum()
        eta_rows.append({'univ': univ, 'n': len(dfc), 'eta2_cluster': eta('lab'),
                         'eta2_pattern': eta('pat')})
    pd.DataFrame(eta_rows).to_csv(os.path.join(OUT, 'cluster_eta.csv'), index=False, encoding='utf-8-sig')
    return eta_rows


def run_earnings(feats, targets):
    import requests
    H = {'User-Agent': 'Mozilla/5.0'}
    from modules.semi_trigger.kr_calendar import is_kr_trading_day, next_kr_trading_day
    rows = []
    for code in ('005930', '000660'):
        disc, page = [], 1
        while page <= 400:
            js = requests.get(f'https://m.stock.naver.com/api/stock/{code}/disclosure'
                              f'?pageSize=100&page={page}', headers=H, timeout=30).json()
            if not js:
                break
            disc += [x for x in js if '영업(잠정)실적' in x['title'] and '정정' not in x['title']]
            if js[-1]['datetime'] < '2014-06-01':
                break
            page += 1
        ohlc = OPENS[code]
        px = ohlc['close']
        f = feats[code]
        # 분기별 첫 잠정실적만 (v1.1) — 보고 분기 = 공시일 직전 분기말. 같은 분기의 확정치(월말)·
        # 정정 공시는 제외해 분기당 1건
        firsts = {}
        for x in sorted(disc, key=lambda v: v['datetime']):
            t = pd.Timestamp(x['datetime'])
            q_end = (t - pd.offsets.QuarterEnd(1)).normalize() if not t.is_quarter_end else t.normalize()
            firsts.setdefault(q_end, x)
        print(f'[실적] {code} 비정정 공시 {len(disc)}건 → 분기별 첫 잠정 {len(firsts)}건')
        for x in sorted(firsts.values(), key=lambda v: v['datetime']):
            t = pd.Timestamp(x['datetime'])
            if t < EVAL_START:
                continue
            dd = t.date()
            if not (is_kr_trading_day(dd) and t.time() < dtime(15, 30)):
                dd = pd.Timestamp(next_kr_trading_day(dd)).date()
            D_ = pd.Timestamp(dd)
            if D_ not in px.index:
                continue
            i = px.index.get_loc(D_)
            if i < 21 or i + 21 > len(px):
                continue
            kind = '분기 첫 잠정'
            rows.append({'code': code, 'disclosed': t, 'kind': kind, 'D': dd,
                         'pre20': (px.iloc[i - 1] / px.iloc[i - 21] - 1) * 100,
                         'react': (px.iloc[i] / px.iloc[i - 1] - 1) * 100,
                         # 진입 E = D 다음 거래일 시가, h일 후 = E부터 h번째 거래일 종가
                         'post1': (px.iloc[i + 1] / ohlc['open'].iloc[i + 1] - 1) * 100,
                         'post5': (px.iloc[i + 5] / ohlc['open'].iloc[i + 1] - 1) * 100,
                         'post20': ((px.iloc[i + 20] / ohlc['open'].iloc[i + 1] - 1) * 100
                                    if i + 20 < len(px) else np.nan),
                         'pat_before': f['pat_cum20'].get(px.index[i - 1]),
                         'pat_after': f['pat_cum20'].get(px.index[min(i + 20, len(px) - 1)])})
    ev = pd.DataFrame(rows)
    ev.to_csv(os.path.join(OUT, 'earnings_events.csv'), index=False, encoding='utf-8-sig')
    return ev


OPENS = {}


async def load_opens():
    """ka10081 수정 시가·종가 (2014-07~) — 실적 이벤트 진입가용."""
    from datetime import datetime
    from api.daily_candle import fn_ka10081
    from modules.semi_trigger.token_provider import get_semi_token
    tk = await get_semi_token()
    for code in ('005930', '000660'):
        rows, base = {}, datetime.now().strftime('%Y%m%d')
        for _ in range(8):
            r = await fn_ka10081(code, base_dt=base, token=tk, silent=True)
            new = {str(c['date']): (float(c['open']), float(c['close'])) for c in r.get('candles', [])
                   if c.get('close') and str(c['date']) not in rows}
            if not new:
                break
            rows.update(new)
            base = (pd.Timestamp(min(new)) - pd.Timedelta(days=1)).strftime('%Y%m%d')
            if min(new) <= '20140601':
                break
        df = pd.DataFrame.from_dict(rows, orient='index', columns=['open', 'close'])
        df.index = pd.to_datetime(df.index)
        OPENS[code] = df.sort_index()


def bh(df):
    v = df['p'].notna()
    ps = df.loc[v, 'p'].sort_values()
    m, cut = len(ps), 0.0
    for i, (_, p) in enumerate(ps.items(), 1):
        if p <= 0.10 * i / m:
            cut = p
    df['fdr10'] = v & (df['p'] <= cut) & (cut > 0)
    return cut, m


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    aggs = load_aggregates()
    targets = target_index()
    feats = {u: features(a[a.index >= EVAL_START - pd.Timedelta(days=200)]) for u, a in aggs.items()}
    feats = {u: f[f.index >= EVAL_START] for u, f in feats.items()}
    # Σ주권 대조 — 시장 단위 원천 대비 비율 (생존편향 크기, 연도별 중앙값)
    ms, mk = aggs['market_sum'], aggs['market']
    j = pd.concat([ms['frgn'].rename('sum'), mk['frgn'].rename('mkt'),
                   ms['tv'].rename('tv_sum'), mk['tv'].rename('tv_mkt')], axis=1).dropna()
    yr = j.groupby(j.index.year).apply(lambda g: pd.Series({
        'days': len(g), 'tv_ratio_median': (g['tv_sum'] / g['tv_mkt']).median(),
        'frgn_corr': g['sum'].corr(g['mkt']),
        'frgn_abs_dev_median_eok': ((g['sum'] - g['mkt']).abs() / 100).median()}))
    yr.to_csv(os.path.join(OUT, 'market_sum_vs_market.csv'), encoding='utf-8-sig')
    print('[Σ주권 vs 시장 단위] 연도별 거래대금 비율·외인 상관:')
    print(yr.round(3).to_string())
    feats.pop('market_sum', None)
    run_patterns(feats, targets)
    run_absorption(feats, targets)
    run_exhaustion(feats, targets)
    run_external(feats, targets)
    eta = run_cluster(feats, targets)
    import asyncio
    asyncio.run(load_opens())
    ev = run_earnings(feats, targets)
    df = pd.DataFrame(CELLS)
    cut, m = bh(df)
    df['sign_ok'] = np.sign(df['d_h1']) == np.sign(df['d_h2'])
    df['top2_ok'] = np.sign(df['d_ex_top2']) == np.sign(df['diff'])
    df['sample_ok'] = (df['n'] >= 30) & ((df['episodes'] >= 10) | df['episodes'].isna())
    df['pass'] = df['fdr10'] & df['sign_ok'] & df['top2_ok'] & df['sample_ok']
    df.to_csv(os.path.join(OUT, 'cells.csv'), index=False, encoding='utf-8-sig')
    reg_path = os.path.join(D, 'verify', 'cell_registry.json')
    reg = json.load(open(reg_path, encoding='utf-8')) if os.path.exists(reg_path) else {
        'studies': {'IC v4 (2026-10-06)': 144, '4-1 상태조합 (2026-10-07)': 136,
                    'mu_sox 신호 (2026-10-07)': 6, 'us_weight v4 (2026-10-09)': 10,
                    '방향 v2 (2026-10-09)': 15}}
    reg['studies']['Phase 5 수급 v1 (2026-10-10)'] = int(m)
    reg['cumulative'] = sum(reg['studies'].values())
    json.dump(reg, open(reg_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    from statistics import NormalDist
    t_bonf = -NormalDist().inv_cdf(0.05 / (2 * reg['cumulative']))
    print(f"Phase 5 셀 {m} | BH-FDR 10% 임계 p ≤ {cut:.5f} | 누적 셀 {reg['cumulative']} → Bonferroni |t|* {t_bonf:.2f}")
    print(f"FDR 통과 {int(df['fdr10'].sum())} | 최종 합격 {int(df['pass'].sum())}")
    show = df[df['fdr10'] | (df['p'] < 0.01)].sort_values('p')
    cols = ['study', 'univ', 'cond', 'target', 'n', 'episodes', 'diff', 'p', 'd_h1', 'd_h2', 'd_ex_top2', 'pass']
    print(show[cols].round(4).to_string(index=False))
    print('\n군집 η² (r20):', [{k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()} for r in eta])
    print(f'\n실적 이벤트 {len(ev)}건 — earnings_events.csv (참고)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
