# -*- coding: utf-8 -*-
"""V1 외부 출처 대조 — 일일 브리프 지시서 (2026-10-09).

운영 출처 vs 독립 외부 출처, 최근 60 공통 거래일 대조. 불일치는 일자별 CSV로 저장하고
원인을 분류한다 (수정주가 / 시차 / 잠정치 / 배당조정 / 정의차이 / 기타).

| 항목                      | 운영 출처            | 외부 출처                         |
| KOSPI 종가·낙폭·3개월      | ka20006 parquet      | yfinance ^KS11                    |
| 삼전·하닉 시가·종가·MA     | ka10081 (수정주가)   | pykrx (수정주가)                  |
| 종목 외인·기관 순매수(수량) | ka10059 (수량)       | 네이버 모바일 trend API           |
| 시장 합계 외인 순매수      | ka10066 합           | 미확보 (pykrx 로그인 필요·네이버 410) |
| A/D (당일 상승·하락 종목수) | MDC 패널             | FDR StockListing (교집합 종목)    |
| 패널 종가 (표본 30종목)    | MDC 패널             | pykrx (수정 전)                   |
| MU·SNDK 종가·수익률        | yfinance             | Nasdaq API                        |
| 미10Y                     | yfinance ^TNX        | FRED DGS10 (FDR 경유)             |
| 원달러                    | yfinance KRW=X       | FRED DEXKOUS (FDR 경유)           |

출력: config/data/verify/v1_<항목>.csv, v1_summary.json
실행: beelink에서 python tools/verify_v1_external.py
"""
import asyncio
import json
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import requests

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
OUT = os.path.join(ROOT, 'config', 'data', 'verify')
N = 60
H = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
STOCKS = (('005930', '삼성전자'), ('000660', 'SK하이닉스'))
SUMMARY = []


def _num(s) -> float:
    s = str(s).replace(',', '').replace('+', '').replace('$', '').replace('%', '').strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def _norm(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def compare(item: str, a: pd.Series, b: pd.Series, tol: float, unit: str,
            note: str = '', last_is_provisional: bool = False,
            special_days: dict = None) -> dict:
    """a(운영) vs b(외부) — 최근 N 공통일. special_days: {date: 원인} 사전 분류."""
    a = a.copy()
    b = b.copy()
    a.index, b.index = _norm(a.index), _norm(b.index)
    df = pd.concat([a.rename('a'), b.rename('b')], axis=1).dropna().iloc[-N:]
    df['err'] = df['a'] - df['b']
    df['abs_err'] = df['err'].abs()
    df['fail'] = df['abs_err'] > tol + 1e-9
    b_prev, b_next = b.shift(1), b.shift(-1)
    causes = []
    ratio = (df['a'] / df['b']).where(df['fail'])
    adj_like = df['fail'].sum() >= 3 and ratio.dropna().std() < 1e-4 \
        and abs(ratio.dropna().mean() - 1) > 1e-6
    for d, row in df.iterrows():
        if not row['fail']:
            causes.append('')
            continue
        if special_days and d in special_days:
            causes.append(special_days[d])
        elif adj_like:
            causes.append('수정주가')
        elif any(abs(row['a'] - s.get(d, np.nan)) <= tol + 1e-9 for s in (b_prev, b_next)):
            causes.append('시차')
        elif last_is_provisional and d == df.index[-1]:
            causes.append('잠정치')
        else:
            causes.append('기타')
    df['cause'] = causes
    os.makedirs(OUT, exist_ok=True)
    fname = item.replace(' ', '_').replace('/', '_').replace('·', '_')
    df.to_csv(os.path.join(OUT, f'v1_{fname}.csv'), encoding='utf-8-sig')
    cnt = df.loc[df['fail'], 'cause'].value_counts().to_dict()
    res = {'item': item, 'n': int(len(df)), 'n_fail': int(df['fail'].sum()),
           'max_err': round(float(df['abs_err'].max()), 6) if len(df) else None,
           'tol': tol, 'unit': unit, 'pass': bool(len(df) and not df['fail'].any()),
           'causes': cnt, 'note': note,
           'range': f"{df.index.min().date()}~{df.index.max().date()}" if len(df) else ''}
    SUMMARY.append(res)
    return res


def unavailable(item: str, why: str):
    SUMMARY.append({'item': item, 'n': 0, 'n_fail': 0, 'max_err': None, 'tol': None,
                    'unit': '', 'pass': None, 'causes': {}, 'note': f'미확보 — {why}',
                    'range': ''})


# ── 1) KOSPI ────────────────────────────────────────────────
def check_kospi():
    import yfinance as yf
    k = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'kospi_daily_ohlc.parquet'))
    ka = k['close'].astype(float)
    y = yf.Ticker('^KS11').history(period='3y', interval='1d', auto_adjust=False)['Close']
    y.index = _norm(y.index)
    compare('KOSPI 종가', ka, y, 0.005, 'pt',
            note='지수 소수 2자리 반올림 허용(0.005)', last_is_provisional=True)
    for label, fn in (('KOSPI 고점대비', lambda c: (c / c.rolling(250).max() - 1) * 100),
                      ('KOSPI 3개월', lambda c: c.pct_change(63) * 100)):
        compare(label, fn(ka), fn(y), 0.01, '%p')


# ── 2) 삼전·하닉 시가·종가·MA ───────────────────────────────
async def check_kr_stocks(token):
    from api.daily_candle import fn_ka10081
    from pykrx import stock
    for code, name in STOCKS:
        r = await fn_ka10081(code, base_dt=datetime.now().strftime('%Y%m%d'),
                             token=token, silent=True)
        kd = pd.DataFrame(r['candles'])
        kd.index = pd.to_datetime(kd['date'])
        kd = kd.sort_index()
        px = stock.get_market_ohlcv('20240101', datetime.now().strftime('%Y%m%d'), code,
                                    adjusted=True)
        px.index = _norm(px.index)
        compare(f'{name} 시가', kd['open'].astype(float), px['시가'].astype(float), 0, '원')
        compare(f'{name} 종가', kd['close'].astype(float), px['종가'].astype(float), 0, '원')
        for w in (60, 200):
            compare(f'{name} MA{w}', kd['close'].astype(float).rolling(w).mean(),
                    px['종가'].astype(float).rolling(w).mean(), 1.0, '원')


# ── 3) 종목 외인·기관 순매수 (수량) ─────────────────────────
async def check_flows(token):
    from utils.rate_limiter import requests as kreq
    import utils.config as config
    for code, name in STOCKS:
        r = await kreq.post(
            config.get_host_url() + '/api/dostk/stkinfo',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {token}', 'cont-yn': 'N',
                     'next-key': '', 'api-id': 'ka10059'},
            json={'dt': datetime.now().strftime('%Y%m%d'), 'stk_cd': code,
                  'amt_qty_tp': '2', 'trde_tp': '0', 'unit_tp': '1'})
        rows = (r.json() or {}).get('stk_invsr_orgn') or []
        kd = pd.DataFrame([{'date': pd.Timestamp(str(x['dt'])),
                            'frgn': _num(x.get('frgnr_invsr')),
                            'orgn': _num(x.get('orgn'))} for x in rows]).set_index('date')
        nv = requests.get(f'https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60',
                          headers=H, timeout=30).json()
        nd = pd.DataFrame([{'date': pd.Timestamp(x['bizdate']),
                            'frgn': _num(x['foreignerPureBuyQuant']),
                            'orgn': _num(x['organPureBuyQuant'])} for x in nv]).set_index('date')
        compare(f'{name} 외인순매수(수량)', kd['frgn'], nd['frgn'], 0, '주')
        compare(f'{name} 기관순매수(수량)', kd['orgn'], nd['orgn'], 0, '주')
    unavailable('시장 합계 외인 순매수', 'pykrx 투자자별 거래실적은 KRX 로그인 필요, 네이버 시장 페이지 410')


# ── 4) A/D·패널 종가 ────────────────────────────────────────
def check_breadth():
    import FinanceDataReader as fdr
    from pykrx import stock
    panel = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'breadth_close_panel.parquet'))
    last = panel.index.max()
    L = fdr.StockListing('KRX')
    L = L[L['Market'].isin(['KOSPI', 'KOSDAQ', 'KOSDAQ GLOBAL'])].set_index('Code')
    common = [c for c in L.index if c in panel.columns]
    chg_p = panel[common].pct_change(fill_method=None).iloc[-1]
    chg_l = L.loc[common, 'ChagesRatio'].astype(float)
    both = pd.concat([chg_p.rename('panel'), chg_l.rename('listing')], axis=1).dropna()
    sign_p, sign_l = np.sign(both['panel'].round(6)), np.sign(both['listing'])
    up_p, dn_p = int((sign_p > 0).sum()), int((sign_p < 0).sum())
    up_l, dn_l = int((sign_l > 0).sum()), int((sign_l < 0).sum())
    mism = both[sign_p != sign_l]
    os.makedirs(OUT, exist_ok=True)
    mism.to_csv(os.path.join(OUT, 'v1_AD_부호불일치.csv'), encoding='utf-8-sig')
    SUMMARY.append({
        'item': 'A/D 당일 종목수 (교집합)', 'n': int(len(both)),
        'n_fail': int(abs(up_p - up_l) + abs(dn_p - dn_l)),
        'max_err': int(len(mism)), 'tol': 0, 'unit': '종목',
        'pass': up_p == up_l and dn_p == dn_l, 'causes': {'부호불일치': int(len(mism))},
        'note': (f'{last.date()} 패널 상승{up_p}/하락{dn_p} vs 상장스냅샷 상승{up_l}/하락{dn_l} | '
                 f'패널 전체 {panel.shape[1]}열 중 상장 교집합 {len(common)} — 유니버스 차이 별도'),
        'range': str(last.date())})
    unavailable('200일선 위 종목 비율', '전종목 장기 독립 원천 없음(pykrx 전종목 로그인 필요) — 표본 종가 대조 + V2 재구현으로 대체')
    # 표본 종가 30종목 — 패널 데이터 자체 검증
    rng = np.random.default_rng(42)
    sample = list(rng.choice(common, size=min(30, len(common)), replace=False))
    rows = []
    for code in sample:
        try:
            px = stock.get_market_ohlcv((last - pd.Timedelta(days=100)).strftime('%Y%m%d'),
                                        last.strftime('%Y%m%d'), code, adjusted=False)
            px.index = _norm(px.index)
            pc = panel[code].dropna()
            j = pd.concat([pc.rename('a'), px['종가'].astype(float).rename('b')],
                          axis=1).dropna().iloc[-N:]
            for d, rr in j.iterrows():
                rows.append({'code': code, 'date': d, 'a': rr['a'], 'b': rr['b'],
                             'fail': abs(rr['a'] - rr['b']) > 0})
        except Exception as e:
            rows.append({'code': code, 'date': None, 'a': None, 'b': None, 'fail': None,
                         'err': str(e)[:60]})
    sd = pd.DataFrame(rows)
    sd.to_csv(os.path.join(OUT, 'v1_패널종가_표본.csv'), encoding='utf-8-sig', index=False)
    ok = sd.dropna(subset=['fail'])
    SUMMARY.append({
        'item': '패널 종가 (표본 30종목)', 'n': int(len(ok)), 'n_fail': int(ok['fail'].sum()),
        'max_err': float((ok['a'] - ok['b']).abs().max()) if len(ok) else None,
        'tol': 0, 'unit': '원', 'pass': bool(len(ok) and not ok['fail'].any()),
        'causes': {'불일치종목': sorted(set(ok.loc[ok['fail'], 'code']))[:10]},
        'note': 'pykrx 수정 전 종가 기준', 'range': ''})


# ── 5) MU·SNDK ──────────────────────────────────────────────
def check_us():
    import yfinance as yf
    for sym in ('MU', 'SNDK'):
        raw = yf.Ticker(sym).history(period='6mo', interval='1d', auto_adjust=False)
        adj = yf.Ticker(sym).history(period='6mo', interval='1d', auto_adjust=True)['Close']
        raw.index, adj.index = _norm(raw.index), _norm(adj.index)
        js = requests.get(f'https://api.nasdaq.com/api/quote/{sym}/historical?assetclass=stocks'
                          f'&fromdate=2026-04-01&todate={datetime.now():%Y-%m-%d}&limit=200',
                          headers=H, timeout=30).json()
        nd = pd.Series({pd.Timestamp(datetime.strptime(x['date'], '%m/%d/%Y')): _num(x['close'])
                        for x in js['data']['tradesTable']['rows']}).sort_index()
        compare(f'{sym} 종가', raw['Close'], nd, 0.01, '$')
        # 수익률 — 운영은 auto_adjust=True. 배당락일은 조정 차이로 분류
        divs = raw['Dividends'][raw['Dividends'] > 0].index
        compare(f'{sym} 수익률', adj.pct_change() * 100, nd.pct_change() * 100, 0.01, '%p',
                note='운영(배당조정) vs Nasdaq(원종가)',
                special_days={d: '배당조정' for d in _norm(divs)})


# ── 6) 미10Y·원달러 ─────────────────────────────────────────
def check_macro():
    import yfinance as yf
    import FinanceDataReader as fdr
    tnx = yf.Ticker('^TNX').history(period='6mo', interval='1d')['Close']
    krw = yf.Ticker('KRW=X').history(period='6mo', interval='1d')['Close']
    dgs = fdr.DataReader('FRED:DGS10', '2026-04-01')['DGS10']
    dex = fdr.DataReader('FRED:DEXKOUS', '2026-04-01')['DEXKOUS']
    compare('미10Y', tnx, dgs, 0.01, '%p',
            note='^TNX(CBOE 종가) vs DGS10(재무부 고시) — 정의차이·FRED 발표 시차 수일')
    compare('원달러', krw, dex, 0.5, '원',
            note='KRW=X(현지 종가) vs DEXKOUS(뉴욕 정오 매입률) — 기준시각 차이')


async def main() -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    steps = [('KOSPI', check_kospi), ('미국종목', check_us), ('거시', check_macro),
             ('폭', check_breadth)]
    for label, fn in steps:
        try:
            fn()
        except Exception as e:
            unavailable(label, f'실행 실패 {type(e).__name__}: {str(e)[:80]}')
    for label, coro in (('KR종목', check_kr_stocks), ('수급', check_flows)):
        try:
            await coro(token)
        except Exception as e:
            unavailable(label, f'실행 실패 {type(e).__name__}: {str(e)[:80]}')

    with open(os.path.join(OUT, 'v1_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(SUMMARY, f, ensure_ascii=False, indent=1, default=str)
    print(f'{"항목":<24}{"n":>4}{"실패":>5}{"최대오차":>12}{"허용":>8}  판정  원인')
    print('-' * 96)
    for s in SUMMARY:
        verdict = '—' if s['pass'] is None else ('통과' if s['pass'] else '실패')
        me = '' if s['max_err'] is None else f"{s['max_err']:.4g}{s['unit']}"
        tol = '' if s['tol'] is None else f"{s['tol']}"
        print(f"{s['item']:<24}{s['n']:>4}{s['n_fail']:>5}{me:>12}{tol:>8}  {verdict:<4}  "
              f"{s['causes'] or ''} {s['note']}")
    print(f'\n저장: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
