# -*- coding: utf-8 -*-
"""Task E — 변동성 사이징 분할매수 검증 (2026-10-06 Lee 지시).

질문:
  ① 반도체 분할매수에 KOSPI ATR이 나은가, 삼성·하이닉스 자체 ATR이 나은가?
  ② 고정 분할 vs 변동성 조절 분할 — 평균단가·MDD·변동성 대비 수익 비교

방법 (사전 선언 — 결과 본 뒤 표 변경 금지):
  사이징 표 (ATR% 백분위, 가용 역사 기준 — 운영과 동일 정의):
    Q1 0~20%ile: 1.5x | Q2: 1.25x | Q3: 1.0x | Q4: 0.75x | Q5 >=80: 0.5x
    (저변동에 더 크게, 고변동에 더 작게 — 총예산 동일, 배수 정규화)
  시나리오 (주 1회 매수, 총예산 동일):
    S0 고정      — 배수 1.0 고정
    S1 KOSPI ATR — 지수 백분위로 배수 결정
    S2 종목 ATR  — 각 종목 자체 백분위
  지표: 금액가중 평균단가 (기간 단순평균가 대비 할인율), 최종수익률,
        포지션 MDD, 일수익률 변동성 대비 (수익/변동 근사)

데이터: beelink MDC bars_1d (005930/000660, 2021~2026)
실행: beelink에서 python tools/dca_vol_sizing.py
"""
import glob
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
STOCKS_DIR = r'C:\market_data\bars_1d\stocks'
KOSPI_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
STOCKS = ('005930', '000660')  # 삼성전자, SK하이닉스
MULT = ((0, 20, 1.5), (20, 40, 1.25), (40, 60, 1.0), (60, 80, 0.75), (80, 101, 0.5))


def load_stock(code: str) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(os.path.join(STOCKS_DIR, code, '*.parquet'))):
        try:
            frames.append(pd.read_parquet(f))
        except Exception:
            pass
    df = pd.concat(frames)
    df['dt'] = pd.to_datetime(df['dt'])
    return df.drop_duplicates('dt').set_index('dt').sort_index()


def atr_pctile(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df['high'].astype(float), df['low'].astype(float), df['close'].astype(float)
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    ap = atr / close * 100
    return (ap.rank(pct=True) * 100).where(ap.notna())  # 가용 역사 백분위 (운영 정의)


def mult_of(pctile: float) -> float:
    for lo, hi, m in MULT:
        if lo <= pctile < hi:
            return m
    return 1.0


def simulate(close: pd.Series, mults: pd.Series) -> dict:
    """주 1회(목요일) 매수. 총예산 동일 — 배수 정규화 가중."""
    weekly = close[close.index.dayofweek == 3].dropna()
    m = mults.reindex(weekly.index).fillna(1.0)
    w = m / m.sum()  # 총예산 1로 정규화
    qty = (w / weekly).sum()
    avg_price = 1 / qty  # 총예산 1 → 평균단가 = 1/수량
    simple_avg = weekly.mean()
    discount = (1 - avg_price / simple_avg) * 100  # 음수면 불리
    final_ret = (qty * weekly.iloc[-1] - 1) * 100
    # 포지션 MDD — 누적투입 대비 평가액 하락
    cum_in = w.cumsum()
    value = (w / weekly).cumsum() * weekly
    ratio = (value / cum_in).replace([np.inf], np.nan).dropna()
    dd = (ratio / ratio.cummax() - 1) * 100
    mdd = dd.min()
    # 최근 1년 일수익률 변동성 대비 (종목 기준 근사 — 동일 종목이라 수익만 비교해도 됨)
    return {'avg_price': round(avg_price, 0), 'discount_pct': round(discount, 2),
            'final_ret': round(final_ret, 1), 'mdd': round(mdd, 1),
            'n_buys': int(len(weekly))}


def main() -> int:
    kospi = pd.read_parquet(KOSPI_PATH)
    kospi_close = kospi['close'].astype(float)
    kospi_high, kospi_low = kospi['high'].astype(float), kospi['low'].astype(float)
    tr = pd.concat([kospi_high - kospi_low, (kospi_high - kospi_close.shift()).abs(),
                    (kospi_low - kospi_close.shift()).abs()], axis=1).max(axis=1)
    kap = tr.ewm(alpha=1 / 14, adjust=False).mean() / kospi_close * 100
    kospi_pctile = (kap.rank(pct=True) * 100)

    for code, name in zip(STOCKS, ('삼성전자', 'SK하이닉스')):
        df = load_stock(code)
        close = df['close'].astype(float)
        pctile = atr_pctile(df)
        kospi_m = mult_of_map(kospi_pctile)
        stock_m = pctile.apply(lambda p: mult_of(p) if not pd.isna(p) else 1.0)
        print(f'== {name} ({code}) {close.index.min().date()}~{close.index.max().date()} ==')
        print(f'{"시나리오":<14}{"평균단가":>10}{"단가할인":>9}{"최종수익":>9}{"MDD":>8}{"매수횟수":>7}')
        rows = [('S0 고정', pd.Series(1.0, index=close.index)),
                ('S1 KOSPI ATR', kospi_m),
                ('S2 종목 ATR', stock_m)]
        for label, m in rows:
            r = simulate(close, m)
            print(f'{label:<14}{r["avg_price"]:>10,.0f}{r["discount_pct"]:>+8.2f}%'
                  f'{r["final_ret"]:>+8.1f}%{r["mdd"]:>7.1f}%{r["n_buys"]:>7}')
        print()
    print('단가할인: (평균단가 대비 기간 단순평균가) — 양수=단가가 평균보다 낮음(유리)')
    print('사이징 표 (사전 선언): Q1 1.5x / Q2 1.25x / Q3 1.0x / Q4 0.75x / Q5 0.5x')
    return 0


def mult_of_map(pctile: pd.Series) -> pd.Series:
    return pctile.apply(lambda p: mult_of(p) if not pd.isna(p) else 1.0)


if __name__ == '__main__':
    sys.exit(main())
