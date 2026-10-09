# -*- coding: utf-8 -*-
"""일일 브리프 계산 모듈 — 순수 함수 (I/O 없음, V2·V3 검증 대상).

정의 (모두 명시 — V2 독립 재계산과 대조):
  us_sessions_for(E)   KR 실행일 E 직전 KR 거래일 P 마감 이후 ~ E 개장 전에 끝나는 미국 세션.
                       미국 날짜 d ∈ [P, E-1] 중 실제 미국 거래일 (세션 d는 KST d+1 05~06시 종료).
  cum_return           세션 직전 종가 대비 마지막 세션 종가 복리 수익률(%) = Π(1+r_i) − 1
  weighted_us_return   Σ w_i × cum_i (종목별 가중치, 결측 심볼은 가중 재분배)
  rolling_beta         β_t = Cov(x,y)/Var(x), 창 = t−window..t−1 (당일 미포함, look-ahead 금지),
                       공분산·분산 모두 ddof=1 (기존 도구의 np.cov/np.var 자유도 불일치 수정)
  zscore_prior         z_t = (v_t − mean(prior window)) / std(prior window, ddof=1), 당일 미포함
  pctile_strict        (history < v).mean() × 100 — scipy percentileofscore(kind='strict')와 동일
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd


def us_sessions_for(kr_exec: date, us_trading_days, prev_kr_trading_day) -> list:
    """KR 실행일 kr_exec에 반영될 미국 세션 날짜 목록 (오름차순).

    us_trading_days: 실제 미국 거래일 집합/리스트 (datetime.date) — 미국 휴장 자동 반영.
    prev_kr_trading_day: kr_calendar.prev_kr_trading_day
    """
    p = pd.Timestamp(prev_kr_trading_day(kr_exec)).date()
    us = set(us_trading_days)
    out, d = [], p
    while d <= kr_exec - timedelta(days=1):
        if d in us:
            out.append(d)
        d += timedelta(days=1)
    return out


def cum_return(closes: pd.Series, sessions: list) -> float:
    """세션 목록의 복리 누적 수익률 (%). closes: index=date(datetime.date) 정렬 종가."""
    if not sessions:
        return None
    idx = list(closes.index)
    first = sessions[0]
    if first not in closes.index or sessions[-1] not in closes.index:
        return None
    i0 = idx.index(first)
    if i0 == 0:
        return None
    base = float(closes.iloc[i0 - 1])
    last = float(closes.loc[sessions[-1]])
    return (last / base - 1) * 100


def weighted_us_return(cum_by_sym: dict, weights: dict) -> float:
    pairs = [(cum_by_sym.get(s), w) for s, w in weights.items()]
    valid = [(r, w) for r, w in pairs if r is not None and not np.isnan(r)]
    if not valid:
        return None
    return sum(r * w for r, w in valid) / sum(w for _, w in valid)


def rolling_beta(x: pd.Series, y: pd.Series, window: int = 60) -> pd.Series:
    """β_t — 창 t−window..t−1 (당일 미포함). ddof=1 일관."""
    df = pd.concat([x.rename('x'), y.rename('y')], axis=1)
    cov = df['x'].rolling(window).cov(df['y'])
    var = df['x'].rolling(window).var()
    return (cov / var).shift(1)


def zscore_prior(s: pd.Series, window: int = 20) -> pd.Series:
    mu = s.rolling(window).mean().shift(1)
    sd = s.rolling(window).std().shift(1)
    return (s - mu) / sd


def pctile_strict(history: pd.Series, v: float) -> float:
    h = history.dropna()
    return float((h < v).mean() * 100) if len(h) else None
