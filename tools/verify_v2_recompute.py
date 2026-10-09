# -*- coding: utf-8 -*-
"""V2 독립 재계산 — 일일 브리프 지시서 (2026-10-09).

같은 원천 데이터, 다른 구현으로 최근 60일 대조:
  ① ATR14·ADX14·+DI·−DI  : market_breadth.adx_atr  vs  ta 라이브러리
  ② z-score             : 운영 3종(semi calc_zscore / us_signal.rolling_z / macro z5)  vs  순수 numpy
  ③ %ile                : market_breadth 산식  vs  scipy.stats.percentileofscore(kind='strict')
  ④ 롤링 β              : brief.calc.rolling_beta  vs  statsmodels OLS  (+ 기존 도구 ddof 편향 측정)
  ⑤ 가중 US 수익률·휴장 누적 복리 : brief.calc  vs  Nasdaq 원종가 수작업 픽스처 3건
출력: config/data/verify/v2_summary.json
"""
import json
import os
import sys
import warnings
from datetime import date

import numpy as np
import pandas as pd
import requests

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
OUT = os.path.join(ROOT, 'config', 'data', 'verify')
N = 60
SUMMARY = []
H = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}


def record(item, a, b, tol, unit, note=''):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~(np.isnan(a) | np.isnan(b))
    err = np.abs(a[m] - b[m])
    SUMMARY.append({'item': item, 'n': int(m.sum()),
                    'n_fail': int((err > tol + 1e-12).sum()),
                    'max_err': float(err.max()) if len(err) else None, 'tol': tol,
                    'unit': unit, 'pass': bool(len(err) and (err <= tol + 1e-12).all()),
                    'note': note})


def check_ta():
    import ta
    from market_breadth import adx_atr
    o = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'kospi_daily_ohlc.parquet'))
    mine = adx_atr(o)
    atr_t = ta.volatility.AverageTrueRange(o['high'], o['low'], o['close'], window=14) \
        .average_true_range()
    adx_i = ta.trend.ADXIndicator(o['high'], o['low'], o['close'], window=14)
    tail = slice(-N, None)
    record('ATR14 (pt)', mine['atr'].iloc[tail], atr_t.iloc[tail], 0.5, 'pt',
           'Wilder 평활 — 초기화 방식 차이는 3,600봉 경과로 소멸')
    record('ATR14 (%)', (mine['atr'] / o['close'] * 100).iloc[tail],
           (atr_t / o['close'] * 100).iloc[tail], 0.5, '%p')
    record('ADX14', mine['adx'].iloc[tail], adx_i.adx().iloc[tail], 0.5, '')
    record('+DI14', mine['plus_di'].iloc[tail], adx_i.adx_pos().iloc[tail], 0.5, '')
    record('-DI14', mine['minus_di'].iloc[tail], adx_i.adx_neg().iloc[tail], 0.5, '')


def check_zscore():
    import yfinance as yf
    from modules.semi_trigger.scoring import calc_zscore
    from modules.semi_trigger.us_signal import rolling_z
    mu = yf.Ticker('MU').history(period='1y', interval='1d', auto_adjust=True)['Close']
    r = (mu.pct_change() * 100).dropna().reset_index(drop=True)
    w = 20
    # numpy 순수 구현: 직전 w개(당일 미포함), ddof=1
    np_z = [np.nan] * len(r)
    vals = r.values
    for i in range(w, len(vals)):
        base = vals[i - w:i]
        np_z[i] = (vals[i] - base.mean()) / base.std(ddof=1)
    np_z = np.array(np_z)
    # (a) semi calc_zscore — baseline = 직전 20개
    semi_z = [np.nan] * len(r)
    for i in range(w, len(vals)):
        z = calc_zscore(list(vals[i - w:i]), vals[i])
        semi_z[i] = np.nan if z is None else z
    record('z: semi calc_zscore', np.array(semi_z)[-N:], np_z[-N:], 1e-9, 'σ',
           '정의: 직전 20(당일 미포함)·평균차감·ddof=1')
    # (b) us_signal.rolling_z
    record('z: us_signal.rolling_z', rolling_z(r, w).values[-N:], np_z[-N:], 1e-3, 'σ',
           '정의 동일, 운영값 소수 3자리 반올림 (허용 1e-3)')
    # (c) macro 단기 z5 — 정의가 다름: σ = 직전 60개 5일수익률(당일 포함)·평균차감 없음
    from macro_monitor import compute_short_term
    k = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'kospi_daily_ohlc.parquet'))
    closes = {d.strftime('%Y-%m-%d'): float(v) for d, v in k['close'].items()}
    st = compute_short_term(closes)
    c = k['close'].astype(float).values
    ret5 = (c[-1] / c[-6] - 1) * 100
    r5 = (pd.Series(c).pct_change(5) * 100).values
    sigma = np.std(r5[-60:], ddof=1)
    record('z: macro 단기 z5 (오늘)', [st['z5']], [round(ret5 / sigma, 2)], 1e-9, 'σ',
           '정의 상이 — σ=직전 60개 5일수익률(당일 포함), 평균차감 없음 (ret5/σ)')


def check_pctile():
    from scipy.stats import percentileofscore
    from market_breadth import adx_atr, snapshot
    o = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'kospi_daily_ohlc.parquet'))
    ax = adx_atr(o)
    atr_s = (ax['atr'] / o['close'] * 100).dropna()
    mine = snapshot(None, o)['atr14_pctile']
    ref = percentileofscore(atr_s.values, atr_s.iloc[-1], kind='strict')
    record('ATR %ile (운영 snapshot)', [mine], [round(ref)], 0.5, '%ile',
           f'운영은 정수 반올림 | scipy {ref:.3f} — 정의: 전체 가용 이력 대비 strict')
    # 최근 60일 각각 — 그날까지 이력만 (point-in-time)
    pit_mine, pit_ref = [], []
    for i in range(len(atr_s) - N, len(atr_s)):
        hist = atr_s.iloc[:i + 1]
        v = hist.iloc[-1]
        pit_mine.append(float((hist < v).mean() * 100))
        pit_ref.append(percentileofscore(hist.values, v, kind='strict'))
    record('ATR %ile (point-in-time 60일)', pit_mine, pit_ref, 1e-9, '%ile')


def check_beta():
    import statsmodels.api as sm
    import yfinance as yf
    from brief.calc import rolling_beta
    mu = yf.Ticker('MU').history(period='2y', interval='1d', auto_adjust=True)['Close']
    sam = yf.Ticker('005930.KS').history(period='2y', interval='1d', auto_adjust=False)
    x = (mu.pct_change() * 100).dropna()
    x.index = x.index.tz_localize(None).normalize()
    sam.index = sam.index.tz_localize(None).normalize()
    gap = (sam['Open'] / sam['Close'].shift(1) - 1) * 100
    # 정렬: 미국 d → 한국 다음 날 (V2는 구현 대조가 목적 — 같은 정렬을 양쪽에 사용)
    y = gap.shift(-1).reindex(x.index)
    df = pd.concat([x.rename('x'), y.rename('y')], axis=1).dropna()
    mine = rolling_beta(df['x'], df['y'], 60)
    ref, legacy = [], []
    for i in range(len(df) - N, len(df)):
        win = df.iloc[i - 60:i]
        ref.append(sm.OLS(win['y'], sm.add_constant(win['x'])).fit().params['x'])
        legacy.append(np.cov(win['x'], win['y'])[0, 1] / np.var(win['x']))
    record('롤링 β (brief.calc vs statsmodels)', mine.iloc[-N:].values, ref, 0.01, '')
    record('롤링 β (기존 도구 np.cov/np.var vs statsmodels)', legacy, ref, 0.01, '',
           '기존 v3~v5 도구 — ddof 불일치로 β가 n/(n−1)=1.0169배 과대')


def check_fixtures():
    import yfinance as yf
    from brief.calc import cum_return, us_sessions_for, weighted_us_return
    from modules.semi_trigger.kr_calendar import prev_kr_trading_day
    from modules.semi_trigger.semi_config import STOCK_US_WEIGHTS
    closes, us_days = {}, set()
    for s in ('MU', 'SNDK'):
        h = yf.Ticker(s).history(period='3mo', interval='1d', auto_adjust=False)['Close']
        h.index = [d.date() for d in h.index.tz_localize(None)]
        closes[s] = h
        us_days |= set(h.index)
    nas = {}
    for s in ('MU', 'SNDK'):
        js = requests.get(f'https://api.nasdaq.com/api/quote/{s}/historical?assetclass=stocks'
                          f'&fromdate=2026-09-01&todate=2026-10-09&limit=60',
                          headers=H, timeout=30).json()
        nas[s] = {pd.Timestamp(x['date']).date(): float(x['close'].replace('$', '').replace(',', ''))
                  for x in js['data']['tradesTable']['rows']}
    cases = [
        ('평일 1일', date(2026, 10, 8), [date(2026, 10, 7)], date(2026, 10, 6)),
        ('휴장 1일 (10/5 대체공휴일)', date(2026, 10, 6),
         [date(2026, 10, 2), date(2026, 10, 5)], date(2026, 10, 1)),
        ('연휴 3일 (추석)', date(2026, 9, 28),
         [date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)], date(2026, 9, 22)),
    ]
    rows = []
    for label, exec_d, expect_sessions, base_d in cases:
        sessions = us_sessions_for(exec_d, us_days, prev_kr_trading_day)
        ok_sess = sessions == expect_sessions
        for code, ws in STOCK_US_WEIGHTS.items():
            mine = weighted_us_return({s: cum_return(closes[s], sessions) for s in ws}, ws)
            # 수작업: Nasdaq 원종가 — (마지막 세션 종가 / 세션 직전 종가 − 1), 가중합
            hand = sum(w * (nas[s][expect_sessions[-1]] / nas[s][base_d] - 1) * 100
                       for s, w in ws.items())
            rows.append({'case': label, 'code': code, 'sessions_ok': ok_sess,
                         'sessions': [str(d) for d in sessions],
                         'calc': round(mine, 4), 'hand': round(hand, 4)})
    for r in rows:
        print(f"  [{r['case']}] {r['code']} 세션 {r['sessions']} {'✓' if r['sessions_ok'] else '✗'}"
              f" | calc {r['calc']:+.4f}% vs 수작업 {r['hand']:+.4f}%")
    record('가중 US 수익률·누적 복리 (픽스처 3건×2종목)', [r['calc'] for r in rows],
           [r['hand'] for r in rows], 0.01, '%p',
           '세션 매핑 ' + ('전부 일치' if all(r['sessions_ok'] for r in rows) else '불일치 있음'))


def main() -> int:
    for label, fn in (('ta', check_ta), ('z', check_zscore), ('pctile', check_pctile),
                      ('beta', check_beta), ('fixture', check_fixtures)):
        try:
            fn()
        except Exception as e:
            SUMMARY.append({'item': label, 'n': 0, 'n_fail': 0, 'max_err': None, 'tol': None,
                            'unit': '', 'pass': False,
                            'note': f'실행 실패 {type(e).__name__}: {str(e)[:100]}'})
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'v2_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(SUMMARY, f, ensure_ascii=False, indent=1, default=str)
    print(f'\n{"항목":<44}{"n":>4}{"실패":>5}{"최대오차":>12}{"허용":>8}  판정  비고')
    print('-' * 110)
    for s in SUMMARY:
        me = '' if s['max_err'] is None else f"{s['max_err']:.3g}"
        print(f"{s['item']:<44}{s['n']:>4}{s['n_fail']:>5}{me:>12}{str(s['tol']):>8}  "
              f"{'통과' if s['pass'] else '실패'}  {s['note']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
