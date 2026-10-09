# -*- coding: utf-8 -*-
"""작업 4 — 종목별 가중치 산출 스크립트 (지시서 §작업 4).

최근 120 거래일: 미국 세션 수익률(US D) vs 다음 KR 세션 수익률(KR D+1, kr_calendar 매핑)
→ 상관계수·베타·R² (심볼×종목). 결과를 config 반영 가능한 형태(JSON)로 저장.

실행: beelink에서 python tools/us_weight_calc.py
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

SYMBOLS = ('MU', 'SNDK', 'WDC', 'STX')
STOCKS = (('005930', '삼성전자', '005930.KS'), ('000660', 'SK하이닉스', '000660.KS'))
LOOKBACK = 120
OUT_JSON = os.path.join(BASE, '..', 'config', 'data', 'us_weights.json')


def load_us_close(days: int = 300) -> pd.DataFrame:
    import yfinance as yf
    cols = {}
    for s in SYMBOLS:
        h = yf.Ticker(s).history(period=f'{days}d', interval='1d', auto_adjust=True)['Close']
        h.index = h.index.tz_localize(None).normalize()
        cols[s] = h
    return pd.DataFrame(cols).dropna(how='all')


def load_kr_close() -> pd.DataFrame:
    import yfinance as yf
    cols = {}
    for code, name, ysym in STOCKS:
        h = yf.Ticker(ysym).history(period='300d', interval='1d', auto_adjust=True)['Close']
        h.index = h.index.tz_localize(None).normalize()
        cols[code] = h
    return pd.DataFrame(cols)


def main() -> int:
    from modules.semi_trigger.kr_calendar import next_kr_trading_day

    us_close = load_us_close()
    kr_close = asyncio.get_event_loop().run_until_complete(load_kr_close())
    us_ret = us_close.pct_change() * 100
    kr_ret = kr_close.pct_change() * 100

    # US D → KR D+1 매핑 (kr_calendar)
    kr_idx = sorted(kr_ret.index)
    kr_set = set(kr_idx)
    mapping = {}
    for d in us_ret.index:
        d_iso = d.date().isoformat()
        k = next_kr_trading_day(d_iso)
        if k and pd.Timestamp(k) in kr_set:
            mapping[d] = pd.Timestamp(k)
    map_s = pd.Series(mapping).sort_index()

    result = {'calculated_at': pd.Timestamp.now().isoformat(timespec='seconds'),
              'lookback_days': LOOKBACK, 'per_symbol': {}}
    print(f'{"심볼":<6}{"종목":<8}{"상관":>8}{"베타":>8}{"R2":>8}{"n":>5}')
    print('-' * 48)
    for sym in SYMBOLS:
        u = us_ret[sym]
        for code, name, _ in STOCKS:
            u_, k_ = [], []
            for d, e in map_s.items():
                if d in u.index and not np.isnan(u[d]) and e in kr_ret.index \
                        and not np.isnan(kr_ret.loc[e, code]):
                    u_.append(u[d])
                    k_.append(kr_ret.loc[e, code])
            u_, k_ = np.array(u_[-LOOKBACK:]), np.array(k_[-LOOKBACK:])
            if len(u_) < 60:
                print(f'{sym:<6}{name:<8} 표본 부족 (n={len(u_)})')
                continue
            corr = np.corrcoef(u_, k_)[0, 1]
            beta = np.cov(u_, k_)[0, 1] / np.var(u_)
            r2 = corr ** 2
            result['per_symbol'][f'{sym}_{code}'] = {
                'corr': round(float(corr), 3), 'beta': round(float(beta), 3),
                'r2': round(float(r2), 3), 'n': len(u_)}
            print(f'{sym:<6}{name:<8}{corr:>8.3f}{beta:>8.3f}{r2:>8.3f}{len(u_):>5}')
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print()
    print(f'저장: {OUT_JSON} — semi_config.STOCK_US_WEIGHTS 갱신 근거')
    return 0


if __name__ == '__main__':
    sys.exit(main())
