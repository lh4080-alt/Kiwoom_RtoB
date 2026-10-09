# -*- coding: utf-8 -*-
"""브리프 작업 3 — 예상 갭 (KR 전일종가→시가) + 사후 기록.

예상 갭_E = β_E × 가중 US 수익률_E
  가중 US 수익률_E = Σ w_i × (실행일 E에 반영되는 미국 세션들의 복리 누적 수익률)_i
  β_E = 60일 롤링 (KR 갭 ~ 가중 US 수익률), 실행일 E−60..E−1만 사용 (look-ahead 금지)
데이터: 미국 = yfinance (V1 Nasdaq 대조 통과), 한국 = ka10081 수정주가 (V1 pykrx 대조 통과)

사후 기록: config/data/brief/gap_log.jsonl — 예측 시점 기록 후 다음 실행에서 실제 갭 채움.
이력 백필은 β가 과거만 쓰므로 그 자체로 표본 외 성과다 (stats_from_history).

CLI: python brief/expected_gap.py  → 이력 성과 + 오늘 예측 출력 (기록은 --log)
"""
import asyncio
import json
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AUTO)
from brief.calc import cum_return, us_sessions_for, weighted_us_return  # noqa: E402
import brief_config as cfg  # noqa: E402

LOG_PATH = os.path.join(AUTO, '..', 'config', 'data', 'brief', 'gap_log.jsonl')
US_SYMS = ('MU', 'SNDK')


def load_us_closes(period: str = '3y') -> dict:
    import yfinance as yf
    out = {}
    for s in US_SYMS:
        h = yf.Ticker(s).history(period=period, interval='1d', auto_adjust=True)['Close']
        h.index = [d.date() for d in pd.DatetimeIndex(h.index).tz_localize(None)]
        out[s] = h[~pd.Index(h.index).duplicated()].sort_index()
    return out


async def load_kr_ohlc(token: str, codes=('005930', '000660')) -> dict:
    from api.daily_candle import fn_ka10081
    out = {}
    for code in codes:
        r = await fn_ka10081(code, base_dt=datetime.now().strftime('%Y%m%d'),
                             token=token, silent=True)
        df = pd.DataFrame(r['candles'])
        df.index = [pd.Timestamp(str(d)).date() for d in df['date']]
        out[code] = df[['open', 'close']].astype(float).sort_index()
    return out


def build_gap_history(us_closes: dict, kr_ohlc: dict, weights: dict = None,
                      window: int = None) -> pd.DataFrame:
    """실행일별 가중 US 수익률·실제 갭·β·예상 갭 (KR 실제 거래일 연속 기준)."""
    weights = weights or cfg.STOCK_US_WEIGHTS
    window = window or cfg.GAP_BETA_WINDOW
    us_days = set().union(*[set(s.index) for s in us_closes.values()])
    rows = []
    for code, df in kr_ohlc.items():
        days = list(df.index)
        for i in range(1, len(days)):
            e, p = days[i], days[i - 1]
            # 이력 구간은 실제 KR 거래일 열로 직전일을 정한다 (kr_calendar와 동일 결과 — V2 픽스처)
            sessions = sorted(d for d in us_days if p <= d < e)
            cum = {s: cum_return(us_closes[s], sessions) for s in weights[code]}
            x = weighted_us_return(cum, weights[code]) if sessions else None
            gap = (df.loc[e, 'open'] / df.loc[p, 'close'] - 1) * 100
            rows.append({'exec': e, 'code': code, 'n_sessions': len(sessions),
                         'x': x, 'gap': gap})
    h = pd.DataFrame(rows)
    parts = []
    for code, g in h.groupby('code'):
        g = g.sort_values('exec').copy()
        valid = g['x'].notna()
        gv = g[valid]
        cov = gv['x'].rolling(window).cov(gv['gap'])
        var = gv['x'].rolling(window).var()
        beta = (cov / var).shift(1)
        g.loc[valid, 'beta'] = beta.values
        g['exp_gap'] = g['beta'] * g['x']
        g['err'] = g['gap'] - g['exp_gap']
        parts.append(g)
    return pd.concat(parts).sort_values(['exec', 'code']).reset_index(drop=True)


def stats_from_history(hist: pd.DataFrame, last_n: int = None) -> dict:
    """표본 외 성과 — MAE, 방향 일치율, 무예측(0) 대비 MAE."""
    out = {}
    for code, g in hist.dropna(subset=['exp_gap', 'gap']).groupby('code'):
        if last_n:
            g = g.iloc[-last_n:]
        nz = g[(g['gap'] != 0) & (g['exp_gap'] != 0)]
        out[code] = {
            'n': int(len(g)),
            'mae': round(float(g['err'].abs().mean()), 3),
            'mae_naive': round(float(g['gap'].abs().mean()), 3),
            'hit': round(float((np.sign(nz['gap']) == np.sign(nz['exp_gap'])).mean()), 3),
            'range': f"{g['exec'].min()}~{g['exec'].max()}",
        }
    return out


def predict(exec_day: date, us_closes: dict, hist: pd.DataFrame, prev_kr_trading_day,
            weights: dict = None, window: int = None) -> dict:
    """실행일 exec_day의 예상 갭 — 이미 마감된 미국 세션만 반영, 남은 세션 수 표기."""
    weights = weights or cfg.STOCK_US_WEIGHTS
    window = window or cfg.GAP_BETA_WINDOW
    us_days = set().union(*[set(s.index) for s in us_closes.values()])
    p = pd.Timestamp(prev_kr_trading_day(exec_day)).date()
    closed = sorted(d for d in us_days if p <= d < exec_day)
    # 아직 열리지 않은 미국 평일(=예정 세션) — 미국 휴장은 반영 못 함, 근사 표기
    pending = [d for d in pd.bdate_range(p, exec_day - pd.Timedelta(days=1)).date
               if d not in us_days and d > (max(us_days) if us_days else p)]
    out = {'exec': exec_day, 'prev_kr': p, 'sessions': closed, 'pending_sessions': pending,
           'by_code': {}}
    for code, ws in weights.items():
        cum = {s: cum_return(us_closes[s], closed) for s in ws}
        x = weighted_us_return(cum, ws) if closed else None
        g = hist[(hist['code'] == code) & (hist['exec'] < exec_day)].dropna(subset=['x'])
        g = g.iloc[-window:]
        beta = None
        if len(g) >= cfg.GAP_MIN_OBS:
            beta = float(g['x'].cov(g['gap']) / g['x'].var())
        out['by_code'][code] = {
            'cum_by_sym': cum, 'x': x, 'beta': beta, 'n_obs': len(g),
            'exp_gap': (beta * x) if (beta is not None and x is not None) else None}
    return out


def log_prediction(pred: dict, path: str = LOG_PATH):
    """예측 기록 — 같은 (exec, code)는 갱신 (예비→최종 재발송 시 덮어씀)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    if os.path.exists(path):
        rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    for code, v in pred['by_code'].items():
        rec = {'exec': str(pred['exec']), 'code': code, 'x': v['x'], 'beta': v['beta'],
               'exp_gap': v['exp_gap'], 'n_sessions': len(pred['sessions']),
               'pending': len(pred['pending_sessions']),
               'logged_at': datetime.now().isoformat(timespec='seconds')}
        rows = [r for r in rows if not (r['exec'] == rec['exec'] and r['code'] == code)]
        rows.append(rec)
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        for r in sorted(rows, key=lambda r: (r['exec'], r['code'])):
            f.write(json.dumps(r, ensure_ascii=False, default=str) + '\n')
    os.replace(path + '.tmp', path)


def fill_actuals(kr_ohlc: dict, path: str = LOG_PATH) -> dict:
    """기록된 예측에 실제 갭을 채우고 누적 MAE·방향 일치율 반환."""
    if not os.path.exists(path):
        return {}
    rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    for r in rows:
        if r.get('gap') is not None:
            continue
        df = kr_ohlc.get(r['code'])
        e = pd.Timestamp(r['exec']).date()
        if df is None or e not in df.index:
            continue
        i = list(df.index).index(e)
        if i == 0:
            continue
        r['gap'] = (df.iloc[i]['open'] / df.iloc[i - 1]['close'] - 1) * 100
        if r.get('exp_gap') is not None:
            r['err'] = r['gap'] - r['exp_gap']
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + '\n')
    os.replace(path + '.tmp', path)
    out = {}
    done = pd.DataFrame([r for r in rows if r.get('err') is not None])
    for code, g in (done.groupby('code') if len(done) else []):
        nz = g[(g['gap'] != 0) & (g['exp_gap'] != 0)]
        out[code] = {'n': int(len(g)), 'mae': round(float(g['err'].abs().mean()), 3),
                     'hit': round(float((np.sign(nz['gap']) == np.sign(nz['exp_gap'])).mean()), 3)}
    return out


async def _cli():
    from modules.semi_trigger.kr_calendar import (is_kr_trading_day, next_kr_trading_day,
                                                  prev_kr_trading_day)
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    us = load_us_closes()
    kr = await load_kr_ohlc(token)
    hist = build_gap_history(us, kr)
    print('== 이력 표본 외 성과 (β는 과거 60일만) ==')
    for label, n in (('전체', None), ('최근 60일', 60)):
        for code, s in stats_from_history(hist, n).items():
            print(f"  [{label}] {code}: n={s['n']} MAE {s['mae']:.2f}%p "
                  f"(무예측 {s['mae_naive']:.2f}) | 방향 일치 {s['hit'] * 100:.0f}% | {s['range']}")
    today = date.today()
    e = today if is_kr_trading_day(today) else pd.Timestamp(next_kr_trading_day(today)).date()
    pred = predict(e, us, hist, prev_kr_trading_day)
    print(f"\n== 예측: KR 실행 {pred['exec']} (직전 KR {pred['prev_kr']}) | 반영 세션 "
          f"{[str(d) for d in pred['sessions']]} | 남은 세션 {len(pred['pending_sessions'])} ==")
    for code, v in pred['by_code'].items():
        eg = 'N/A' if v['exp_gap'] is None else f"{v['exp_gap']:+.2f}%"
        print(f"  {code}: 가중 US {v['x']:+.2f}% × β {v['beta']:.3f} (n={v['n_obs']}) → 예상 갭 {eg}")
    if '--log' in sys.argv:
        log_prediction(pred)
        print('기록:', LOG_PATH)


if __name__ == '__main__':
    asyncio.run(_cli())
