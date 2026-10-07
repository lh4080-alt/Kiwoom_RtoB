# -*- coding: utf-8 -*-
"""국면별 비중 조절 + 단기 인버스 전환 백테스트 (2026-09-13 Lee 계획 검증).

전략 (모두 일일 리밸런스 모델, 신호는 전일 종가 기준 — 실행가능):
  기준선  : 069500 100% Buy&Hold
  전략 A  : 국면 라벨별 069500 비중 (강세 100% / 보합 60% / 하락 30%), 나머지 현금
  전략 B  : A + 하락장 & 단기 하락 흐름(5일 z ≤ -0.5σ)일 때 그 비중을 인버스(-1x)로 전환.
            인버스 수익 = -(당일 수익률) - 변동성드래그(20일 일간분산) — 드래그 정직 모델.

국면 라벨 = 거시 모니터와 동일 규칙 (200일선 이격/52주고점대비/3개월 모멘텀 다수결) —
가격만으로 계산되므로 2015~2026 전 기간 검증 가능.
채택 기준: 독립 구간 2개 이상 기준선 대비 Sharpe 개선 + MDD 열화 없음 (민감도는 보고 전용).
"""
import asyncio
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dca_intensity_backtest import fetch_daily, CODE  # ka10081 페이징 수집 재사용

W_UP, W_MID, W_DOWN = 1.0, 0.6, 0.3     # 전략 A 국면별 비중 (a priori)
INV_Z_TH = -0.5                          # 단기 하락 흐름 임계 (거시 모니터와 동일)
START, END = '2015-01-01', '2026-09-12'


def build_labels(closes: pd.Series) -> pd.DataFrame:
    """거시 모니터와 동일 규칙의 국면 라벨 + 단기 z (가격만으로)."""
    ma200 = closes.rolling(200).mean()
    trend = (closes / ma200 - 1) * 100
    dd = (closes / closes.rolling(250).max() - 1) * 100
    mom = closes.pct_change(63) * 100

    t_dir = np.where(trend >= 0, 'up', np.where(trend >= -5, 'mid', 'dn'))
    d_dir = np.where(dd >= -10, 'up', np.where(dd >= -20, 'mid', 'dn'))
    m_dir = np.where(mom >= 5, 'up', np.where(mom >= -5, 'mid', 'dn'))
    ups = (t_dir == 'up').astype(int) + (d_dir == 'up').astype(int) + (m_dir == 'up').astype(int)
    dns = (t_dir == 'dn').astype(int) + (d_dir == 'dn').astype(int) + (m_dir == 'dn').astype(int)
    label = np.where(ups >= 2, 'up', np.where(dns >= 2, 'down', 'mid'))

    # 단기 z: 5일 누적을 60일 변동성으로 정규화
    r5 = closes.pct_change(5)
    z5 = (r5 - r5.rolling(60).mean()) / r5.rolling(60).std()

    out = pd.DataFrame({'label': label, 'z5': z5}, index=closes.index)
    # 실행가능성: 오늘의 비중은 어제 종가 시점 신호로 결정
    out['label_sig'] = out['label'].shift(1)
    out['z5_sig'] = out['z5'].shift(1)
    return out


def run_strategy(closes: pd.Series, sig: pd.DataFrame, mode: str,
                 w=(W_UP, W_MID, W_DOWN), start=START, end=END):
    """mode: 'bh' | 'A'(국면 비중) | 'B'(A+인버스). 일일 리밸런스 복리."""
    ret = closes.pct_change()
    var20 = ret.rolling(20).var()          # 변동성 드래그용 일간분산
    w_up, w_mid, w_dn = w
    idx = closes.loc[start:end].index
    equity, daily = [], []
    e = 1.0
    for t in idx:
        r = ret.get(t)
        if r is None or np.isnan(r):
            daily.append(0.0); equity.append(e); continue
        lab = sig.at[t, 'label_sig'] if mode != 'bh' else 'up'
        z = sig.at[t, 'z5_sig']
        if mode == 'bh':
            w_long, use_inv = 1.0, False
        else:
            w_long = w_up if lab == 'up' else (w_mid if lab == 'mid' else w_dn)
            use_inv = (mode == 'B') and (lab == 'down') and (z is not None) and (z <= INV_Z_TH)
        if use_inv:
            drag = var20.get(t, 0.0)
            if drag is None or np.isnan(drag):
                drag = 0.0
            port = w_long * (-(r) - drag)          # 위험 비중 → 인버스
        else:
            port = w_long * r                       # 위험 비중 → 현물
        e *= (1 + port)
        daily.append(port)
        equity.append(e)
    eq = pd.Series(equity, index=idx)
    dr = pd.Series(daily, index=idx)
    years = (idx[-1] - idx[0]).days / 365.25
    cagr = (eq.iloc[-1]) ** (1 / years) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    sharpe = float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else np.nan
    return {'CAGR': cagr, 'MDD': mdd, 'Sharpe': sharpe, 'final': eq.iloc[-1], 'n': len(idx)}


def row(name, res):
    return {'전략': name, 'CAGR': f"{res['CAGR']*100:+.2f}%", 'MDD': f"{res['MDD']*100:.1f}%",
            'Sharpe': f"{res['Sharpe']:.2f}", '최종배율': f"{res['final']:.2f}"}


async def main():
    print('[1/3] 데이터 수집 (ka10081 페이징)...')
    token_mod_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation')
    sys.path.insert(0, token_mod_path)
    from modules.semi_trigger.token_provider import get_semi_token
    closes = await fetch_daily(await get_semi_token())
    print(f"  {len(closes)}행 {closes.index[0].date()} ~ {closes.index[-1].date()}")

    print('[2/3] 국면 라벨 계산...')
    sig = build_labels(closes)

    windows = [('전체 2015-26', START, END),
               ('2015-2019', '2015-01-01', '2019-12-31'),
               ('2020-2022', '2020-01-01', '2022-12-31'),
               ('2023-2026', '2023-01-01', END)]

    print('[3/4] 전략별 검증 (일일 리밸런스, 신호=전일 종가 — 실행가능)\n')
    for wname, ws, we in windows:
        rows = []
        rows.append(row('기준선 Buy&Hold', run_strategy(closes, sig, 'bh', start=ws, end=we)))
        rows.append(row('A: 국면별 비중 100/60/30', run_strategy(closes, sig, 'A', start=ws, end=we)))
        rows.append(row('B: A+단기 인버스', run_strategy(closes, sig, 'B', start=ws, end=we)))
        print(f"=== [{wname}] ===")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    print('=== 민감도 (전체 기간, 보고 전용 — 골라잡기 금지) ===')
    rows = []
    for w in ((1.0, 0.6, 0.3), (1.0, 0.5, 0.0), (1.0, 0.7, 0.4)):
        rows.append(row(f"A 비중 {w}", run_strategy(closes, sig, 'A', w=w)))
        rows.append(row(f"B 비중 {w}+인버스", run_strategy(closes, sig, 'B', w=w)))
    print(pd.DataFrame(rows).to_string(index=False))

    print('\n=== 국면 라벨 분포 (전체 기간) ===')
    lab = sig['label_sig'].dropna()
    print((lab.value_counts(normalize=True) * 100).round(1).to_string())


if __name__ == '__main__':
    asyncio.run(main())
