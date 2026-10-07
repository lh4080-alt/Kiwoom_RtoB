# -*- coding: utf-8 -*-
"""§4-2 v2 — 추세 필터 재검증: 홀드아웃 오염 수정 + 노출 조정 + 파라미터 스윕 (2026-10-07).

v1 오염 기록 (지시서 규칙 — 이력 보존):
  v1은 2012~2026-10 전 기간(홀드아웃 2025-09~ 포함)으로 실행됨 → 4-2에 한해
  홀드아웃은 최종 확정용으로 사용 불가. 본 v2는 학습 구간(~2025-09-11)만 사용.
  4-1의 홀드아웃은 별개 봉인 유지 (4-2와 축이 다르지만 결과 공유 없음 확인용).

사전 고정:
  체결 t종가신호→t+1시가, 비용 0.15%/매매, 현금 금리 연 2.5% (일할, CMA 가정)
  노출 조정 기준선: 평균 노출 = 필터의 평균 포지션인 고정 비중 포트 (같은 현금 금리 적용)
  스윕: MA {150,180,200,250} × 밴드 {0, ±2%, ±3%} (히스테리시스 — 진입 >MA×(1+b), 이탈 <MA×(1-b))
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
COST = 0.0015
CASH_RATE_DAILY = 0.025 / 252
LEARN_END = pd.Timestamp('2025-09-11')  # 4-1 홀드아웃 직전 — 학습 구한


def load_stock(code):
    frames = [pd.read_parquet(f) for f in
              sorted(glob.glob(os.path.join(STOCKS_DIR, code, '*.parquet')))]
    df = pd.concat(frames)
    df['dt'] = pd.to_datetime(df['dt'])
    return df.drop_duplicates('dt').set_index('dt').sort_index()


def hysteresis_pos(close: pd.Series, ma_w: int, band: float) -> pd.Series:
    """히스테리시스 필터: 미보유 → close>MA×(1+band) 진입 / 보유 → close<MA×(1-band) 이탈."""
    ma = close.rolling(ma_w).mean()
    upper, lower = ma * (1 + band), ma * (1 - band)
    pos, cur = [], 0.0
    for c, u, l in zip(close.values, upper.values, lower.values):
        if np.isnan(u):
            pos.append(0.0)
            continue
        if cur == 0 and c > u:
            cur = 1.0
        elif cur == 1 and c < l:
            cur = 0.0
        pos.append(cur)
    return pd.Series(pos, index=close.index)


def evaluate(df: pd.DataFrame, pos: pd.Series, label: str) -> dict:
    pos = pos.reindex(df.index).fillna(0.0)
    prev = pos.shift(1).fillna(0.0)
    trade = (pos != prev).astype(float)
    net = pos * df['ret_oo'] - trade * COST + (1 - pos) * CASH_RATE_DAILY
    eq = (1 + net).cumprod()
    years = (df.index[-1] - df.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1
    dd = (eq / eq.cummax() - 1).min() * 100
    vol = net.std() * np.sqrt(252)
    t_idx = df.index[trade == 1]
    whipsaw = sum(1 for a, b in zip(t_idx, t_idx[1:])
                  if df.index.get_loc(b) - df.index.get_loc(a) <= 5)
    return {'label': label, 'cagr': cagr * 100, 'mdd': dd,
            'rpv': (cagr / vol if vol else np.nan), 'expo': pos.mean() * 100,
            'trades': int(trade.sum()), 'whipsaw': whipsaw}


def main() -> int:
    kospi = pd.read_parquet(KOSPI_PATH)
    targets = [('KOSPI', kospi['close'].astype(float), kospi['open'].astype(float),
                kospi['high'].astype(float), kospi['low'].astype(float))]
    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        df = load_stock(code)
        targets.append((name, df['close'].astype(float), df['open'].astype(float),
                        df['high'].astype(float), df['low'].astype(float)))

    print(f'학습 구간: ~{LEARN_END.date()} (홀드아웃 2025-09-12~ 봉인 — 4-2는 v1 오염으로 이 구간만 사용)\n')
    for name, close, open_, high, low in targets:
        c = close[close.index <= LEARN_END]
        o = open_.reindex(c.index)
        h, l = high.reindex(c.index), low.reindex(c.index)
        df = pd.DataFrame({'close': c, 'open': o, 'ret_oo': o.pct_change()}).dropna(subset=['ret_oo'])
        df['ret_oo'] = o.pct_change()
        df = df.iloc[1:]
        # 원 필터 (v1 동일) + F0 + 노출 조정 기준선들
        ma200, ma20, ma60 = c.rolling(200).mean(), c.rolling(20).mean(), c.rolling(60).mean()
        f1 = (c > ma200).astype(float).shift(1)
        f2 = (ma20 > ma60).astype(float).shift(1)
        rows = []
        rows.append(evaluate(df, pd.Series(1.0, index=df.index), 'F0 기준선(100%)'))
        rows.append(evaluate(df, f1, 'F1 200일선'))
        rows.append(evaluate(df, f2, 'F2 20·60일선'))
        # 노출 조정 기준선 — F1의 평균 노출과 같은 고정 비중 + 현금 금리
        f1_expo = f1.mean()
        rows.append(evaluate(df, pd.Series(f1_expo, index=df.index),
                             f'고정 {f1_expo * 100:.0f}% (노출조정)'))
        print(f'== {name} (학습 n={len(df)}) ==')
        print(f'{"전략":<24}{"CAGR":>8}{"MDD":>9}{"수익/변동":>9}{"평균노출":>8}{"매매":>5}{"whip":>5}')
        for r in rows:
            print(f'{r["label"]:<24}{r["cagr"]:>7.1f}%{r["mdd"]:>8.1f}%{r["rpv"]:>9.2f}'
                  f'{r["expo"]:>7.0f}%{r["trades"]:>5}{r["whipsaw"]:>5}')

        # 파라미터 스윕 — 200 근처 완만성 확인
        print('-- MA×밴드 스윕 (CAGR% / MDD% / 수익변동 / whip) --')
        for ma_w in (150, 180, 200, 250):
            line = []
            for band in (0.0, 0.02, 0.03):
                pos = hysteresis_pos(c, ma_w, band).shift(1)
                r = evaluate(df, pos, '')
                line.append(f'b{band:.2f}: {r["cagr"]:.1f}/{r["mdd"]:.1f}/{r["rpv"]:.2f}/{r["whipsaw"]}')
            print(f'   MA{ma_w:>3}: ' + ' | '.join(line))
        print()
    print('기록: v1(전 기간 실행)은 홀드아웃 포함 — 4-2 홀드아웃 무효. 본 결과는 학습 구간만.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
