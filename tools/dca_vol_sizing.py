# -*- coding: utf-8 -*-
"""Task E v2 — 변동성 사이징 분할매수 재검증 (2026-10-06 Lee 지시: 룩어헤드 제거+강건성).

v1 결함 수정:
  - 전체 표본 rank(룩어헤드) → 252일 롤링 백분위 + shift(1) (전일 값으로 결정)
  - 미집행 현금: 배수 정규화(합=1)로 총투입 동일 — 현금 발생 없음 (명시)
비교 축 (같은 하네스):
  S0 고정 | S1 KOSPI ATR | S2 종목 ATR | S3 MA60이격도 | S4 고점대비
  S5 결합(ATR+이격도) | S2역전(고변동에 더 크게) | S2완화(1.25~0.75x)
강건성:
  - 연도별 단가 개선 분해
  - 시작일 시프트(0~51주) × 12/24개월 창 — 승률·평균·최악 개선폭
  - 블록 부트스트랩(52주 블록, 1000회) CI
사전 선언 표: Q1 1.5 / Q2 1.25 / Q3 1.0 / Q4 0.75 / Q5 0.5 (완화: 1.25/1.1/1.0/0.9/0.75)
실행: beelink에서 python tools/dca_vol_sizing.py
"""
import glob
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
STOCKS_DIR = r'C:\market_data\bars_1d\stocks'
KOSPI_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
STOCKS = (('005930', '삼성전자'), ('000660', 'SK하이닉스'))
WIN = 252  # 백분위 롤링 창 (1년 — 전일까지 데이터만)


def load_stock(code):
    frames = [pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(STOCKS_DIR, code, '*.parquet')))]
    df = pd.concat(frames)
    df['dt'] = pd.to_datetime(df['dt'])
    return df.drop_duplicates('dt').set_index('dt').sort_index()


def rpct(s, win=WIN):
    """롤링 백분위 + shift(1) — 결정 시점에 전일까지 데이터만 사용 (룩어헤드 방지)."""
    return (s.rolling(win, min_periods=120).rank(pct=True) * 100).shift(1)


def stock_feats(df):
    close, high, low = (df[k].astype(float) for k in ('close', 'high', 'low'))
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_pct = tr.ewm(alpha=1 / 14, adjust=False).mean() / close * 100
    disp60 = close / close.rolling(60).mean() - 1
    dd250 = close / close.rolling(250).max() - 1
    return {'atr': rpct(atr_pct), 'disp': rpct(disp60), 'dd': rpct(dd250)}


def mult(p, table):
    if pd.isna(p):
        return np.nan
    for lo, hi, m in table:
        if lo <= p < hi:
            return m
    return np.nan


TBL = ((0, 20, 1.5), (20, 40, 1.25), (40, 60, 1.0), (60, 80, 0.75), (80, 101, 0.5))
TBL_INV = tuple((lo, hi, 2.0 - m) for lo, hi, m in TBL)          # 역전: 합 2.5 유지
TBL_SOFT = ((0, 20, 1.25), (20, 40, 1.1), (40, 60, 1.0), (60, 80, 0.9), (80, 101, 0.75))


def disc_of(seg: pd.Series, ms: pd.Series) -> float:
    """금액가중 단가 할인 (%) — 총예산 1 정규화."""
    wl = ms.reindex(seg.index, method='ffill').fillna(1.0)
    wl = wl / wl.sum()
    ql = (wl / seg).sum()
    return (1 - (1 / ql) / seg.mean()) * 100


def weekly_buy(close, mults_daily):
    """주 목요일 매수. 결정 배수 = 매수일 직전 거래일 값 (ffill). 총예산 1 정규화 — 현금 없음."""
    weekly = close[close.index.dayofweek == 3].dropna()
    m = mults_daily.reindex(weekly.index, method='ffill')
    m = m.fillna(1.0)
    w = m / m.sum()
    qty = (w / weekly).sum()
    avg_price = 1 / qty
    simple = weekly.mean()
    discount = (1 - avg_price / simple) * 100
    value = (w / weekly).cumsum() * weekly
    ratio = (value / w.cumsum()).replace([np.inf], np.nan).dropna()
    mdd = ((ratio / ratio.cummax() - 1) * 100).min()
    return discount, mdd, qty


def main() -> int:
    kospi = pd.read_parquet(KOSPI_PATH)
    kclose = kospi['close'].astype(float)
    khigh, klow = kospi['high'].astype(float), kospi['low'].astype(float)
    ktr = pd.concat([khigh - klow, (khigh - kclose.shift()).abs(),
                     (klow - kclose.shift()).abs()], axis=1).max(axis=1)
    kospi_atr_p = rpct(ktr.ewm(alpha=1 / 14, adjust=False).mean() / kclose * 100)

    for code, name in STOCKS:
        df = load_stock(code)
        close = df['close'].astype(float)
        f = stock_feats(df)
        schemes = {
            'S0 고정': pd.Series(1.0, index=close.index),
            'S1 KOSPI ATR': kospi_atr_p.apply(lambda p: mult(p, TBL)),
            'S2 종목 ATR': f['atr'].apply(lambda p: mult(p, TBL)),
            'S3 MA60이격도': f['disp'].apply(lambda p: mult(p, TBL)),
            'S4 고점대비': f['dd'].apply(lambda p: mult(p, TBL)),
            'S5 결합(ATR+이격)': None,  # 아래에서 평균
            'S2역전(고변동多)': f['atr'].apply(lambda p: mult(p, TBL_INV)),
            'S2완화': f['atr'].apply(lambda p: mult(p, TBL_SOFT)),
        }
        schemes['S5 결합(ATR+이격)'] = pd.concat([schemes['S2 종목 ATR'],
                                                  schemes['S3 MA60이격도']], axis=1).mean(axis=1)
        print(f'== {name} ({code}) {close.index.min().date()}~{close.index.max().date()} '
              f'| 룩어헤드 없음: 252일 롤링 백분위 + 전일 값 ==')
        print(f'{"시나리오":<16}{"단가할인%":>9}{"최종수익%":>9}{"MDD%":>7}')
        res = {}
        for label, m in schemes.items():
            d, mdd, qty = weekly_buy(close, m)
            res[label] = (d, mdd)
            final = (qty * close.iloc[-1] - 1) * 100
            print(f'{label:<16}{d:>9.2f}{final:>+9.1f}{mdd:>7.1f}')
        base = res['S0 고정'][0]

        # ── 연도별 분해 (S2 종목 ATR) ──
        print('-- 연도별 단가 개선 (S2-S0, %p) --')
        parts = []
        for y in sorted(close.index.year.unique()):
            sub = close[close.index.year == y]
            if len(sub[sub.index.dayofweek == 3]) < 8:
                continue
            d0, _, _ = weekly_buy(sub, pd.Series(1.0, index=sub.index))
            d2, _, _ = weekly_buy(sub, schemes['S2 종목 ATR'].reindex(sub.index, method='ffill'))
            parts.append(f'{y}:{d2 - d0:+.2f}')
        print('   ' + ' | '.join(parts))

        # ── 시작일 시프트 × 창 반복 (S2, S3, S5 vs S0) ──
        print('-- 시작일 시프트 52가지 × 창 (개선 %p: 평균 / 최악 / 승률) --')
        weekly_all = close[close.index.dayofweek == 3].dropna()
        for winm in (12, 24):
            n = int(winm * 4.33)
            for label in ('S2 종목 ATR', 'S3 MA60이격도', 'S5 결합(ATR+이격)'):
                imps = []
                for off in range(52):
                    seg = weekly_all.iloc[off:off + n]
                    if len(seg) < n:
                        break
                    w0 = pd.Series(1.0, index=seg.index) / len(seg)
                    imps.append(disc_of(seg, schemes[label]) - disc_of(seg, w0))
                a = np.array(imps)
                print(f'   {winm}개월 {label:<16} 평균 {a.mean():+.2f} / 최악 {a.min():+.2f} '
                      f'/ 승률 {(a > 0).mean() * 100:.0f}% (n={len(a)})')

        # ── 블록 부트스트랩 (52주 블록, 1000회 — 변동성 군집 보존) ──
        rng = np.random.default_rng(42)
        for label in ('S2 종목 ATR', 'S3 MA60이격도'):
            imps = []
            for _ in range(1000):
                starts = rng.integers(0, len(weekly_all) - 52, size=len(weekly_all) // 52 + 1)
                idxs = [i for b in starts for i in range(b, b + 52)][:len(weekly_all)]
                seg = weekly_all.iloc[idxs].reset_index(drop=True)
                ms = schemes[label].iloc[idxs].reset_index(drop=True)
                w0 = pd.Series(1.0, index=seg.index) / len(seg)
                imps.append(disc_of(seg, ms) - disc_of(seg, w0))
            a = np.array(imps)
            lo, hi = np.percentile(a, [2.5, 97.5])
            print(f'   블록부트 {label:<16} 개선 평균 {a.mean():+.2f} [{lo:+.2f},{hi:+.2f}]')
        print()
    print('비고: MDD는 시나리오 간 사실상 동일 — 개선은 단가만. 미집행 현금 없음(배수 합 정규화).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
