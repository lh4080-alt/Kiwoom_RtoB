# -*- coding: utf-8 -*-
"""액티브 전략 2종 백테스트 — 069500 롱 전용 이벤트/스윙 (2026-09-13).

옵션 1 (semi→지수 확장): 미국발 급락 이벤트 → 다음날 종가 매수 → 5거래일 보유
  트리거: 나스닥 5일 누적 z(20일 baseline) ≤ -1.0 (강한딥)
          또는 0.6×나스닥z + 0.4×SOX/NVDA 평균z ≤ -1.0 (일반딥)
  필터: 069500 종가 > 60일선 (semi의 추세필터 동일)
  정렬: 미국 세션 D는 한국 D+1 아침 종료 → 한국 T일 신호 = 미국날짜 ≤ T-1 최신값

옵션 2 (강세장 눌림 스윙): 국면 ∈ {강세,보합} & 단기 5일 z ≤ -0.5 → 진입
  청산: +3% 익절 / -2% 손절 / 10거래일 타임아웃 (종가 기준, 먼저 도달한 것)

공통: 전일 신호 → 당일 종가 진입(실행가능), 동시 1포지션, 롱 전용.
지표: 거래수·승률·평균수익(=기대값)·복리 총수익 vs 동기간 Buy&Hold.
판정: 거래 기대값이 양수이고 2개 이상 독립 구간에서 유지되면 통과.
"""
import asyncio
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dca_intensity_backtest import fetch_daily

WINDOWS = [('전체 2015-26', '2015-01-01', '2026-09-12'),
           ('2015-2019', '2015-01-01', '2019-12-31'),
           ('2020-2022', '2020-01-01', '2022-12-31'),
           ('2023-2026', '2023-01-01', '2026-09-12')]


def fetch_us():
    import yfinance as yf
    out = {}
    for name, sym in {'nq': 'NQ=F', 'sox': '^SOX', 'nvda': 'NVDA'}.items():
        h = yf.Ticker(sym).history(start='2014-01-01', end='2026-09-13', interval='1d', auto_adjust=True)
        s = h['Close'].dropna()
        s.index = pd.to_datetime(s.index).tz_localize(None)
        out[name] = s
    return out


def z20(cum5: pd.Series) -> pd.Series:
    m = cum5.rolling(20).mean()
    sd = cum5.rolling(20).std()
    return (cum5 - m) / sd


def us_z_on_kr_days(us: pd.Series, kr_index: pd.DatetimeIndex) -> pd.Series:
    """미국날짜 z를 한국 거래일 T에 '미국날짜 ≤ T-1 최신값'으로 매핑 (전야 정렬)."""
    us = us.dropna()
    us.index = pd.to_datetime(us.index)
    vals = []
    udates = us.index.sort_values()
    for t in kr_index:
        pos = udates.searchsorted(t - pd.Timedelta(days=1), side='right') - 1
        vals.append(us.iloc[pos] if pos >= 0 else np.nan)
    return pd.Series(vals, index=kr_index)


def option1_signals(kr_closes, us):
    nq5 = us['nq'].pct_change(5)
    z_nq = z20(nq5)
    z_leg = (z20(us['sox'].pct_change(5)) + z20(us['nvda'].pct_change(5))) / 2
    z_nq_kr = us_z_on_kr_days(z_nq, kr_closes.index)
    z_leg_kr = us_z_on_kr_days(z_leg, kr_closes.index)
    ma60 = kr_closes.rolling(60).mean()
    trend_ok = kr_closes > ma60
    sig = ((z_nq_kr <= -1.0) | ((0.6 * z_nq_kr + 0.4 * z_leg_kr) <= -1.0)) & trend_ok
    return sig.shift(1).fillna(False)  # 전일 신호 → 당일 진입


def option2_signals(kr_closes):
    ma200 = kr_closes.rolling(200).mean()
    trend = (kr_closes / ma200 - 1) * 100
    dd = (kr_closes / kr_closes.rolling(250).max() - 1) * 100
    mom = kr_closes.pct_change(63) * 100
    t_up = (trend >= 0).astype(int); d_up = (dd >= -10).astype(int); m_up = (mom >= 5).astype(int)
    t_dn = (trend < -5).astype(int); d_dn = (dd < -20).astype(int); m_dn = (mom < -5).astype(int)
    ups = t_up + d_up + m_up
    dns = t_dn + d_dn + m_dn
    label = np.where(ups >= 2, 'up', np.where(dns >= 2, 'down', 'mid'))
    label = pd.Series(label, index=kr_closes.index)
    r5 = kr_closes.pct_change(5)
    z5 = (r5 - r5.rolling(60).mean()) / r5.rolling(60).std()
    gate = label.isin(['up', 'mid'])
    sig = (z5 <= -0.5) & gate
    return sig.shift(1).fillna(False)


def simulate(kr_closes, entries: pd.Series, hold_days=None,
             take=None, stop=None, time_stop=5, start='2015-01-01', end='2026-09-12'):
    """entries: 당일 진입 여부(True) 시그니즈(전일 신호 shift 완료). 종가 진입/청산."""
    idx = kr_closes.loc[start:end].index
    trades = []
    i = 0
    pos_idx = [j for j, t in enumerate(kr_closes.index) if entries.get(t, False) and t in set(idx)]
    n = len(kr_closes)
    used_until = -1
    for j in pos_idx:
        if j <= used_until or j + 1 >= n:
            continue
        entry = float(kr_closes.iloc[j + 1]) if j + 1 < n else None
        if entry is None:
            continue
        exit_p, exit_i = None, None
        for k in range(j + 2, min(j + 2 + (time_stop or 10), n)):
            p = float(kr_closes.iloc[k])
            chg = p / entry - 1
            if take is not None and chg >= take:
                exit_p, exit_i = p, k
                break
            if stop is not None and chg <= stop:
                exit_p, exit_i = p, k
                break
            if exit_i is None and k == min(j + 2 + (time_stop or 10) - 1, n - 1):
                exit_p, exit_i = p, k
        if exit_p is None:
            exit_p = float(kr_closes.iloc[-1]); exit_i = n - 1
        trades.append({'entry_d': kr_closes.index[j + 1], 'exit_d': kr_closes.index[exit_i],
                       'ret': exit_p / entry - 1})
        used_until = exit_i
    df = pd.DataFrame(trades)
    df = df[(df['entry_d'] >= start) & (df['entry_d'] <= end)]
    return df


def report(name, df, closes, ws, we):
    sub = df[(df['entry_d'] >= ws) & (df['entry_d'] <= we)]
    if sub.empty:
        return {'전략': name, '거래수': 0}
    win = (sub['ret'] > 0).mean()
    eq = (1 + sub['ret']).prod()
    bh = float(closes.asof(pd.Timestamp(we)) / closes.asof(pd.Timestamp(ws)) - 1)
    years = (pd.Timestamp(we) - pd.Timestamp(ws)).days / 365.25
    cagr = eq ** (1 / years) - 1 if eq > 0 else -1
    return {'전략': name, '거래수': len(sub),
            '승률': f"{win*100:.0f}%",
            '평균수익': f"{sub['ret'].mean()*100:+.2f}%",
            '최악': f"{sub['ret'].min()*100:+.1f}%",
            '복리총수익': f"{(eq-1)*100:+.0f}%",
            'CAGR': f"{cagr*100:+.1f}%",
            'B&H CAGR': f"{((1+bh)**(1/years)-1)*100:+.1f}%"}


async def main():
    print('[1/3] 데이터 수집 (069500 + 나스닥/SOX/NVDA)...')
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))
    from modules.semi_trigger.token_provider import get_semi_token
    closes = await fetch_daily(await get_semi_token())
    us = fetch_us()
    print(f"  069500 {len(closes)}행 / US 3종 수집 완료")

    print('[2/3] 신호 계산 + 시뮬레이션...')
    sig1 = option1_signals(closes, us)
    sig2 = option2_signals(closes)
    df1 = simulate(closes, sig1, hold_days=5)
    df2 = simulate(closes, sig2, take=0.03, stop=-0.02, time_stop=10)

    print('[3/3] 결과\n')
    for name, df in (('옵션1: 미국발 급락 → 069500 5일 보유', df1),
                     ('옵션2: 강세장 눌림 스윙 (+3/-2%/10일)', df2)):
        print(f"=== {name} ===")
        rows = [report('결과', df, closes, ws, we) for _, ws, we in WINDOWS]
        print(pd.DataFrame(rows).to_string(index=False))
        if not df.empty:
            print(f"  (전 기간 평균 보유일수: {(pd.to_datetime(df['exit_d'])-pd.to_datetime(df['entry_d'])).dt.days.mean():.1f}일)")
        print()


if __name__ == '__main__':
    asyncio.run(main())
