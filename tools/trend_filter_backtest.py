# -*- coding: utf-8 -*-
"""§4-2 추세 필터 → 비중 조절 규칙 테스트 — 최종작업지시서 (2026-10-07).

충돌 가정 (Lee 지시 — 먼저 명시):
  4-1은 "강한 하락 추세 뒤 반등"을, 추세 필터는 "하락이면 비중 축소"를 말함.
  필터가 반등 구간을 놓쳐 불리할 수 있음 → 낙폭 깊이 구간별 성과를 같이 본다.

사전 고정 (결과 보기 전):
  전략: F0 기준선(항상 보유) | F1 200일선(종가>MA200 보유) | F2 20·60일선(MA20>MA60)
        F3 결합(MA20>MA60 & ADX>=20)
  체결: t일 종가 신호 → t+1 시가 체결 (open-to-open 수익 적용)
  비용: 매매 1회당 0.15% (수수료+세금+슬리피지, ETF 가정 — 전 대상 동일)
  지표: CAGR, 최대낙폭, 수익/변동(일수익 std 대비), 연 매매횟수,
        whipsaw(5거래일 내 반대 전환), 연도별 수익, 낙폭 구간별 성과
  대상: KOSPI(2012~), 삼성전자·SK하이닉스(2021~, MDC 일봉)

실행: beelink에서 python tools/trend_filter_backtest.py
"""
import glob
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
KOSPI_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
STOCKS_DIR = r'C:\market_data\bars_1d\stocks'
COST = 0.0015  # 매매 1회당


def load_stock(code):
    frames = [pd.read_parquet(f) for f in
              sorted(glob.glob(os.path.join(STOCKS_DIR, code, '*.parquet')))]
    df = pd.concat(frames)
    df['dt'] = pd.to_datetime(df['dt'])
    return df.drop_duplicates('dt').set_index('dt').sort_index()


def run(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({'close': close, 'open': open_})
    ma200, ma20, ma60 = close.rolling(200).mean(), close.rolling(20).mean(), close.rolling(60).mean()
    up, dn = high.diff(), -low.diff()
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr.replace(0, np.nan)
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()
    df['f1'] = (close > ma200).astype(float)
    df['f2'] = (ma20 > ma60).astype(float)
    df['f3'] = ((ma20 > ma60) & (adx >= 20)).astype(float)
    df['dd'] = (close / close.rolling(250).max() - 1) * 100
    # open-to-open 수익 (position은 전일 종가 신호 → 당일부터 적용)
    df['ret_oo'] = df['open'].pct_change()
    for f in ('f1', 'f2', 'f3'):
        df[f'pos_{f}'] = df[f].shift(1)
    df['pos_f0'] = 1.0
    return df.dropna(subset=['ret_oo', 'pos_f1'])


def evaluate(df: pd.DataFrame, pos_col: str) -> dict:
    pos = df[pos_col].fillna(0.0)
    prev = pos.shift(1).fillna(0.0)
    trade = (pos != prev).astype(float)
    gross = pos * df['ret_oo']
    net = gross - trade * COST
    eq = (1 + net).cumprod()
    years = (df.index[-1] - df.index[0]).days / 365.25
    cagr = (eq.iloc[-1]) ** (1 / years) - 1
    dd = (eq / eq.cummax() - 1).min() * 100
    vol = net.std() * np.sqrt(252) * 100
    n_trades = int(trade.sum())
    # whipsaw: 5거래일 내 반대 전환
    t_idx = df.index[trade == 1]
    whipsaw = sum(1 for a, b in zip(t_idx, t_idx[1:])
                  if df.index.get_loc(b) - df.index.get_loc(a) <= 5)
    return {'cagr': cagr * 100, 'mdd': dd, 'ret_per_vol': (cagr * 100 / vol if vol else np.nan),
            'trades': n_trades, 'whipsaw': whipsaw, 'eq': eq, 'net': net}


def yearly(df: pd.DataFrame, pos_col: str) -> dict:
    pos = df[pos_col].fillna(0.0)
    prev = pos.shift(1).fillna(0.0)
    net = pos * df['ret_oo'] - (pos != prev) * COST
    return (1 + net).groupby(df.index.year).prod().sub(1).mul(100).round(1).to_dict()


def depth_buckets(df: pd.DataFrame, schemes: list) -> None:
    """낙폭 구간별: 구간 수익(기준선)과 각 전략의 체류 비율·기여."""
    print('-- 낙폭 구간별 (기준선 수익 vs 전략 체류율) --')
    buckets = [(0, -10), (-10, -20), (-20, -30), (-30, -100)]
    for lo, hi in buckets:
        m = (df['dd'] <= lo) & (df['dd'] > hi)
        if m.sum() < 20:
            continue
        seg = df[m]
        seg_ret = (1 + seg['ret_oo']).prod() - 1
        stays = ' | '.join(f"{s[1]}: {seg[s[1]].mean() * 100:.0f}%" for s in schemes)
        print(f'   낙폭 {lo}~{hi}%: n={int(m.sum())} | 구간 수익 {seg_ret * 100:+.1f}% | 체류율 {stays}')


def main() -> int:
    kospi = pd.read_parquet(KOSPI_PATH)
    targets = [('KOSPI', kospi['close'].astype(float), kospi['open'].astype(float),
                kospi['high'].astype(float), kospi['low'].astype(float))]
    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        df = load_stock(code)
        targets.append((name, df['close'].astype(float), df['open'].astype(float),
                        df['high'].astype(float), df['low'].astype(float)))

    schemes = [('F0 기준선', 'pos_f0'), ('F1 200일선', 'pos_f1'),
               ('F2 20·60일선', 'pos_f2'), ('F3 결합(20·60+ADX)', 'pos_f3')]

    for name, close, open_, high, low in targets:
        df = run(close, open_, high, low)
        print(f'== {name} ({df.index.min().date()}~{df.index.max().date()}, n={len(df)}) ==')
        print(f'{"전략":<16}{"CAGR":>8}{"MDD":>9}{"수익/변동":>9}{"매매":>5}{"whipsaw":>8}')
        for label, col in schemes:
            r = evaluate(df, col)
            print(f'{label:<16}{r["cagr"]:>7.1f}%{r["mdd"]:>8.1f}%{r["ret_per_vol"]:>9.2f}'
                  f'{r["trades"]:>5}{r["whipsaw"]:>8}')
        depth_buckets(df, schemes)
        print('-- 연도별 수익 (F1 200일선) --')
        y = yearly(df, 'pos_f1')
        print('   ' + ' | '.join(f'{k}:{v:+.0f}%' for k, v in y.items()))
        print()
    print('비용 0.15%/매매, 체결 t종가신호→t+1시가. 사전 고정 규칙 — 결과 후 변경 시 새 버전.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
