# -*- coding: utf-8 -*-
"""작업 4 검증 v2 — 구간 분리·다변량 회귀·롤링 안정성·조건부 검증 (2026-10-09 Lee 지시).

· 수익률 구간: KR D+1 = (전일종가→시가) / (시가→종가) / (전일종가→종가) 각각 상관·베타·R²
· 다변량: [MU] / [MU+SNDK] / [MU+SOX] / [MU+SNDK+SOX] — R²·증분 R²·계수 t값
· 기간: 가용 최대(SNDK 상장 후) + 60일 롤링 계수 안정성 차트(PNG → 텔레그램)
· 조건부: 트리거일(가중 z≤-1.3 or 가중 수익≤-3.5%)만 — 시가→종가·시가→D+5 평균/승률/n
· 가중치 재산출: 다변량 계수 → 정규화, 증분 R² < 0.01 심볼은 판정 제외
· 휴장 매핑: kr_calendar.next_kr_trading_day 사용

실행: beelink에서 python tools/us_weight_calc_v2.py [png 저장경로]
"""
import asyncio
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
from modules.semi_trigger.kr_calendar import next_kr_trading_day  # noqa: E402

PNG_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    BASE, '..', 'config', 'data', 'us_beta_stability.png')


def ols_t(X: np.ndarray, y: np.ndarray) -> dict:
    """OLS + 계수 t값 (const 포함 X)."""
    Xc = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ beta
    n, k = Xc.shape
    sigma2 = resid @ resid / (n - k)
    se = np.sqrt(np.diag(sigma2 * np.linalg.pinv(Xc.T @ Xc)))
    t_vals = beta / se
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - (resid @ resid) / ss_tot
    return {'beta': beta, 't': t_vals, 'r2': float(r2)}


def load_all() -> tuple:
    import yfinance as yf
    us_syms = {'MU': 'MU', 'SNDK': 'SNDK', 'SOX': '^SOX'}
    us_close = {}
    for k, s in us_syms.items():
        h = yf.Ticker(s).history(period='max', interval='1d', auto_adjust=True)['Close']
        h.index = h.index.tz_localize(None).normalize()
        us_close[k] = h
    us = pd.DataFrame(us_close).dropna(how='all')
    us_ret = us.pct_change() * 100
    kr = {}
    for code in ('005930', '000660'):
        h = yf.Ticker(f'{code}.KS').history(period='max', interval='1d', auto_adjust=False)
        h.index = h.index.tz_localize(None).normalize()
        kr[code] = h[['Open', 'Close']]
    return us_ret, kr


def build_aligned(us_ret: pd.DataFrame, kr: dict) -> pd.DataFrame:
    """US D 수익률 + KR E(D+1) 3구간 수익률 정렬."""
    rows = []
    kr_codes = list(kr.keys())
    for d in us_ret.index:
        e_iso = next_kr_trading_day(d.date().isoformat())
        if not e_iso:
            continue
        e = pd.Timestamp(e_iso)
        for code in kr_codes:
            h = kr[code]
            idx = h.index
            i = np.where(idx >= e)[0]
            if not len(i) or idx[i[0]] != e or i[0] == 0:
                continue
            j = i[0]
            prev_c = h['Close'].iloc[j - 1]
            on = (h['Open'].iloc[j] / prev_c - 1) * 100
            intra = (h['Close'].iloc[j] / h['Open'].iloc[j] - 1) * 100
            full = (h['Close'].iloc[j] / prev_c - 1) * 100
            row = {'us_date': d, 'kr_date': e, 'code': code,
                   'on': on, 'intra': intra, 'full': full}
            for k in us_ret.columns:
                v = us_ret.loc[d, k]
                row[k] = v if not np.isnan(v) else None
            rows.append(row)
    return pd.DataFrame(rows)


def r2_of(X: np.ndarray, y: np.ndarray) -> float:
    if len(X) < len(X[0]) + 2 if X.ndim == 2 else False:
        pass
    r = ols_t(X, y)
    return r['r2']


def main() -> int:
    us_ret, kr = load_all()
    al = build_aligned(us_ret, kr)
    al = al.dropna(subset=['MU', 'SOX'])
    al_sndk = al.dropna(subset=['SNDK'])
    print(f'정렬 표본: 전체 {len(al)}행 (MU+SOX) | SNDK 포함 {len(al_sndk)}행 '
          f'({al_sndk["us_date"].min().date()}~)\n')

    # ── 1) 구간 분리: 단일 심볼별 상관·베타·R² ──
    print('== 1) 구간별 단일심볼 회귀 (MU → KR D+1, 전체 기간) ==')
    print(f'{"구간":<10}{"상관":>8}{"베타":>8}{"R2":>8}')
    seg_stats = {}
    for seg in ('on', 'intra', 'full'):
        x = al['MU'].values
        y = al[seg].values
        corr = np.corrcoef(x, y)[0, 1]
        beta = np.cov(x, y)[0, 1] / np.var(x)
        seg_stats[seg] = (corr, beta)
        print(f'{seg:<10}{corr:>8.3f}{beta:>8.3f}{corr ** 2:>8.3f}')

    # ── 2) 다변량 회귀 — full 구간, 증분 R² + t값 ──
    print()
    print('== 2) 다변량 회귀 [full 구간, SNDK 가용 기간] — 삼전/하닉 ==')
    weights_new = {}
    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        d = al_sndk[al_sndk['code'] == code].dropna(subset=['MU', 'SNDK', 'SOX'])
        y = d['full'].values
        steps = [('MU', ['MU']), ('MU+SNDK', ['MU', 'SNDK']),
                 ('MU+SOX', ['MU', 'SOX']), ('MU+SNDK+SOX', ['MU', 'SNDK', 'SOX'])]
        r2s = {}
        print(f'-- {name} ({len(d)}행) --')
        for label, cols in steps:
            X = d[cols].values
            r = ols_t(X, y)
            r2s[label] = r['r2']
            tstr = ' '.join(f'{c}:t={t:+.1f}' for c, t in zip(['const'] + cols, r['t']))
            print(f'  {label:<14} R2={r["r2"]:.3f} | {tstr}')
        inc_sndk = r2s['MU+SNDK'] - r2s['MU']
        inc_sox = r2s['MU+SNDK+SOX'] - r2s['MU+SNDK']
        print(f'  증분 R²: +SNDK {inc_sndk:+.3f} | +SOX {inc_sox:+.3f}')
        # 가중치 재산출 — MU+SNDK+SOX 풀모형의 MU·SNDK 계수 정규화
        full_r = ols_t(d[['MU', 'SNDK', 'SOX']].values, y)
        b_mu, b_sndk = abs(full_r['beta'][1]), abs(full_r['beta'][2])
        w_sndk = b_sndk / (b_mu + b_sndk)
        w_mu = 1 - w_sndk
        weights_new[code] = {'MU': round(w_mu, 2), 'SNDK': round(w_sndk, 2),
                             'inc_r2_sndk': round(inc_sndk, 4),
                             'exclude_sndk': inc_sndk < 0.01}
        print(f'  → 재산출 가중치: MU {w_mu:.2f} / SNDK {w_sndk:.2f}'
              f'{" (SNDK 제외 — 증분 R²<0.01)" if inc_sndk < 0.01 else ""}')
        print()

    # ── 3) 60일 롤링 베타 안정성 차트 ──
    print('== 3) 60일 롤링 MU 베타 안정성 ==')
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        for ax, code, name in zip(axes, ('005930', '000660'), ('삼성전자', 'SK하이닉스')):
            d = al[al['code'] == code].dropna(subset=['MU']).sort_values('us_date')
            betas, dates = [], []
            for i in range(60, len(d)):
                w = d.iloc[i - 60:i]
                x, y = w['MU'].values, w['full'].values
                if np.var(x) == 0:
                    continue
                betas.append(np.cov(x, y)[0, 1] / np.var(x))
                dates.append(d['us_date'].iloc[i])
            ax.plot(dates, betas, lw=1.2)
            ax.axhline(0, color='gray', lw=0.5)
            ax.set_title(f'{name} — 60일 롤링 MU 베타 (full 구간)')
            ax.grid(alpha=0.3)
            print(f'{name}: 롤링 베타 평균 {np.mean(betas):+.2f} / 표준편차 {np.std(betas):.2f} '
                  f'/ 구간 [{min(betas):+.2f},{max(betas):+.2f}]')
        axes[-1].tick_params(axis='x', rotation=30)
        plt.tight_layout()
        plt.savefig(PNG_PATH, dpi=110)
        print(f'차트 저장: {PNG_PATH}')
    except Exception as e:
        print(f'차트 실패: {e}')

    # ── 4) 조건부 검증 — 트리거일만 ──
    print()
    print('== 4) 조건부 검증 (트리거일만: 가중 z<=-1.3 or 가중 수익<=-3.5%) ==')
    from modules.semi_trigger.semi_config import STOCK_US_WEIGHTS, SIGMA_WINDOW
    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        ws = STOCK_US_WEIGHTS[code]
        d = al[al['code'] == code].copy()
        d['wret'] = d['MU'] * ws['MU'] + d['SNDK'].fillna(0) * ws.get('SNDK', 0) \
            if ws.get('SNDK') else d['MU']
        mu_r = us_ret['MU']
        z_mu = (mu_r - mu_r.rolling(SIGMA_WINDOW).mean().shift(1)) / mu_r.rolling(SIGMA_WINDOW).std().shift(1)
        d['z'] = d['us_date'].map(z_mu)
        trig = d[((d['z'] <= -1.3) | (d['wret'] <= -3.5))]
        for label, col, extra in (('시가→종가', 'on', 0), ('시가→D+5', None, 5)):
            rets = []
            for _, row in trig.iterrows():
                h = kr[code]
                idx = h.index
                i = np.where(idx >= row['kr_date'])[0]
                if not len(i) or idx[i[0]] != row['kr_date']:
                    continue
                j = i[0]
                if extra == 0:
                    rets.append((h['Close'].iloc[j] / h['Open'].iloc[j] - 1) * 100)
                elif j + extra < len(h):
                    rets.append((h['Close'].iloc[j + extra] / h['Open'].iloc[j] - 1) * 100)
            if rets:
                a = np.array(rets)
                print(f'{name} {label}: 평균 {a.mean():+.2f}% / 승률 {(a > 0).mean() * 100:.0f}% (n={len(a)})')
        print(f'  (트리거일 {len(trig)}건 / 전체 {len(d)}일)')

    # 결과 JSON
    with open(os.path.join(BASE, '..', 'config', 'data', 'us_weight_v2.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'weights_new': weights_new,
                   'note': '증분 R²<0.01 심볼은 판정 제외 — semi_config 반영 전 Lee 확인'},
                  f, ensure_ascii=False, indent=1)
    print()
    print('저장: config/data/us_weight_v2.json — semi_config 반영 전 Lee 확인 필요')
    return 0


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop(asyncio.new_event_loop())
    sys.exit(main())
