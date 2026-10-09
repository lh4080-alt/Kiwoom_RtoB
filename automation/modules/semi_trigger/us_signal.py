# -*- coding: utf-8 -*-
"""작업 4 — us_memory 분리 신호 (yfinance 시계열 직접, DB 의존 없음).

그룹: mem_dram(MU) / mem_nand(SNDK) / mem_hdd(WDC·STX, 표시 전용).
z: 20일 롤링 point-in-time (당일 포함 과거만 — 룩어헤드 없음).
판정 신호 = 종목별 가중 합성 z (semi_config.STOCK_US_WEIGHTS).
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '..', 'automation'))

SYMBOLS = ('MU', 'SNDK', 'WDC', 'STX')


def load_us_returns(days: int = 200) -> dict:
    """심볼별 일간 수익률(%) 시계열. 캐시: config/data/us_returns_cache.csv."""
    cache = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), 'config', 'data',
        'us_returns_cache.csv')
    if os.path.exists(cache):
        age_h = (datetime.now().timestamp() - os.path.getmtime(cache)) / 3600
        if age_h < 6:
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
            return {s: df[s] for s in SYMBOLS}
    import yfinance as yf
    cols = {}
    for s in SYMBOLS:
        h = yf.Ticker(s).history(period=f'{days}d', interval='1d', auto_adjust=True)['Close']
        h.index = h.index.tz_localize(None).normalize()
        cols[s] = h.pct_change() * 100
    df = pd.DataFrame(cols).dropna(how='all')
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    df.to_csv(cache)
    return {s: df[s] for s in df.columns}


def rolling_z(rets: pd.Series, window: int = 20) -> pd.Series:
    """당일 수익률의 z — baseline은 직전 window일 (point-in-time)."""
    mu = rets.rolling(window).mean()
    sd = rets.rolling(window).std()
    return ((rets - mu.shift(1)) / sd.shift(1)).round(3)


def composite_z(z_by_symbol: dict, weights: dict) -> float:
    """종목별 가중 합성 z — 유효 심볼만 재분배."""
    pairs = [(z_by_symbol.get(s), w) for s, w in weights.items()]
    valid = [(z, w) for z, w in pairs if z is not None]
    if not valid:
        return None
    return round(sum(z * w for z, w in valid) / sum(w for _, w in valid), 3)


def latest_signal(stock_weights: dict = None, sigma_window: int = None) -> dict:
    """오늘(최신 미국 세션) 분리 신호 — snapshot에서 호출.

    Returns: {'mu_z', 'sndk_z', 'hdd_z', 'hdd_ret', 'mu_ret', 'sndk_ret',
              'comp_z_by_stock': {code: z}, 'us_date'}
    """
    from modules.semi_trigger.semi_config import (STOCK_US_WEIGHTS, SIGMA_WINDOW,
                                                  HDD_INCLUDE_IN_SIGNAL,
                                                  US_MEM_COMPOSITION)
    w = sigma_window or SIGMA_WINDOW
    rets = load_us_returns()
    z = {s: rolling_z(rets[s], w).iloc[-1] if not rets[s].dropna().empty else None
         for s in SYMBOLS}
    ret_last = {s: round(float(rets[s].dropna().iloc[-1]), 2) if not rets[s].dropna().empty
                else None for s in SYMBOLS}
    hdd_z = (round(sum(z[s] for s in US_MEM_COMPOSITION['mem_hdd'] if z[s] is not None)
                   / max(1, sum(1 for s in US_MEM_COMPOSITION['mem_hdd'] if z[s] is not None)), 3)
             if any(z[s] is not None for s in US_MEM_COMPOSITION['mem_hdd']) else None)
    comp = {code: composite_z({'MU': z['MU'], 'SNDK': z['SNDK']}, ws)
            for code, ws in (stock_weights or STOCK_US_WEIGHTS).items()}
    return {'mu_z': z['MU'], 'sndk_z': z['SNDK'], 'hdd_z': hdd_z,
            'mu_ret': ret_last['MU'], 'sndk_ret': ret_last['SNDK'],
            'hdd_ret': round(float(sum(ret_last[s] for s in US_MEM_COMPOSITION['mem_hdd'])
                                   / len(US_MEM_COMPOSITION['mem_hdd'])), 2),
            'comp_z_by_stock': comp,
            'us_date': str(rets['MU'].dropna().index[-1].date())}


if __name__ == '__main__':
    import json
    print(json.dumps(latest_signal(), ensure_ascii=False, indent=1))
