# -*- coding: utf-8 -*-
"""일일 브리프 — 작업 1·2 (메시지 구조 Lee 확정 2026-10-09).

세션 판정 (zoneinfo America/New_York — 서머타임 자동):
  d_last  = 지금(KST) 이전에 마감(16:00 ET)된 마지막 미국 세션
  E       = d_last 다음 KR 거래일 (실행일),  P = E 직전 KR 거래일
  반영    = 미국 세션 ∈ [P, E−1] 중 마감된 것,  남은 세션이 있으면 '예비', 없으면 '확정'
데이터 원천 (V1 대조 결과 반영):
  KOSPI·ATR       ka20006 parquet (매일 16:50 갱신)
  폭(200일선·A/D) MDC 패널 + 최종일은 ka10066 KRX 확정값으로 대체 (MDC 최종일은 15:35 잠정)
                  유니버스 = ka10066 종목(KOSPI·KOSDAQ) 고정 — 폭 정의 v2
  외인 시장       종목별 ka10059 합산 시계열 + 최종일 ka10066 합 (외국인, 기타외국인 제외)
  종목 MA·외인    ka10081 수정주가, ka10059 금액
  미국            yfinance (Nasdaq 대조 통과) · 미10Y ^TNX (네이버 로이터 대조 59/60)
  원달러          yfinance KRW=X — 정의 일치 2차 출처 미확보로 ⚠️ 미검증 (ECOS 키 발급 시 교체)
"""
import asyncio
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AUTO)
import brief_config as cfg  # noqa: E402
from brief.calc import pctile_strict  # noqa: E402

DATA = os.path.join(AUTO, '..', 'config', 'data')
ET, KST = ZoneInfo('America/New_York'), ZoneInfo('Asia/Seoul')


# ── 미국 세션 달력 ────────────────────────────────────────────
def is_us_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in cfg.US_HOLIDAYS


def us_close_kst(d: date) -> datetime:
    """미국 세션 d의 마감(16:00 ET)을 KST로 — 서머타임 자동 반영."""
    return datetime.combine(d, time(16, 0), tzinfo=ET).astimezone(KST)


def last_closed_us_session(now_kst: datetime) -> date:
    d = now_kst.astimezone(ET).date()
    for _ in range(10):
        if is_us_trading_day(d) and us_close_kst(d) <= now_kst:
            return d
        d -= timedelta(days=1)
    return None


def session_frame(now_kst: datetime) -> dict:
    from modules.semi_trigger.kr_calendar import next_kr_trading_day, prev_kr_trading_day
    d_last = last_closed_us_session(now_kst)
    e = pd.Timestamp(next_kr_trading_day(d_last)).date()
    p = pd.Timestamp(prev_kr_trading_day(e)).date()
    window = [p + timedelta(days=i) for i in range((e - p).days)]
    all_us = [d for d in window if is_us_trading_day(d)]
    closed = [d for d in all_us if d <= d_last]
    return {'d_last': d_last, 'exec': e, 'prev_kr': p, 'closed': closed,
            'total': len(all_us), 'final': len(closed) == len(all_us),
            'send_at': us_close_kst(d_last) + timedelta(minutes=cfg.BRIEF_SEND_DELAY_MIN)}


# ── 표기 ─────────────────────────────────────────────────────
def pct(v, sign=True) -> str:
    return 'N/A' if v is None or (isinstance(v, float) and np.isnan(v)) else \
        (f'{v:+.1f}%' if sign else f'{v:.1f}%')


def money_eok(v_eok) -> str:
    """억원 값 → 조/억 표기 (|v| ≥ 1조면 조 1자리)."""
    if v_eok is None or np.isnan(v_eok):
        return 'N/A'
    return f'{v_eok / 1e4:+.1f}조' if abs(v_eok) >= 1e4 else f'{v_eok:+,.0f}억'


def lag_tag(src_date: date, expected: date) -> str:
    if src_date is None:
        return ' (결측)'
    from modules.semi_trigger.kr_calendar import is_kr_trading_day
    n = 0
    d = src_date
    while d < expected:
        d += timedelta(days=1)
        if is_kr_trading_day(d):
            n += 1
    return f' (지연 {n}일)' if n else ''


# ── 데이터 수집 ──────────────────────────────────────────────
async def ka10066_official(token, universe: set = None) -> dict:
    """장마감후 KRX 확정 — {code: {'px', 'flu'}} (KOSPI·KOSDAQ), KOSPI 주권 외인 합 (ETN 제외)."""
    from utils.rate_limiter import requests as kreq
    import utils.config as config

    def num(s):
        s = str(s or '0').replace('+', '')
        if s.startswith('--'):
            s = '-' + s[2:]
        try:
            return float(s)
        except ValueError:
            return np.nan
    out, kospi_frgn = {}, 0.0
    for mkt in ('001', '101'):
        cont, nk = 'N', ''
        for _ in range(60):
            r = await kreq.post(
                config.get_host_url() + '/api/dostk/mrkcond',
                headers={'Content-Type': 'application/json;charset=UTF-8',
                         'authorization': f'Bearer {token}', 'cont-yn': cont,
                         'next-key': nk, 'api-id': 'ka10066'},
                json={'mrkt_tp': mkt, 'amt_qty_tp': '1', 'trde_tp': '0', 'stex_tp': '1'})
            for x in (r.json() or {}).get('opaf_invsr_trde') or []:
                c = str(x['stk_cd']).strip()
                out[c] = {'px': abs(num(x['cur_prc'])), 'flu': num(x['flu_rt'])}
                if mkt == '001' and (universe is None or c in universe):
                    kospi_frgn += num(x['frgnr_invsr'])
            cont, nk = r.headers.get('cont-yn', 'N'), r.headers.get('next-key', '')
            if cont != 'Y':
                break
    return {'codes': out, 'kospi_frgn_eok': kospi_frgn / 100}


async def stock_flows_20d(token, code: str) -> pd.Series:
    """ka10059 외인 순매수 금액 (억원), 최근 100거래일."""
    from utils.rate_limiter import requests as kreq
    import utils.config as config
    r = await kreq.post(
        config.get_host_url() + '/api/dostk/stkinfo',
        headers={'Content-Type': 'application/json;charset=UTF-8',
                 'authorization': f'Bearer {token}', 'cont-yn': 'N', 'next-key': '',
                 'api-id': 'ka10059'},
        json={'dt': datetime.now().strftime('%Y%m%d'), 'stk_cd': code, 'amt_qty_tp': '1',
              'trde_tp': '0', 'unit_tp': '1'})
    rows = (r.json() or {}).get('stk_invsr_orgn') or []
    s = pd.Series({pd.Timestamp(str(x['dt'])).date():
                   float(str(x.get('frgnr_invsr', '0')).replace('+', '') or 0) / 100
                   for x in rows})
    return s.sort_index()


UNIVERSE_CACHE = os.path.join(DATA, 'brief', 'universe_stocks.json')


def stock_universe() -> set:
    """KRX 상장 주권 (KOSPI·KOSDAQ·KOSDAQ GLOBAL) — ETF·ETN 제외 판별용 회원 목록.

    MDC 패널에 ETF·ETN 1,281개, ka10066에 ETN 368개가 섞여 있어 (CD금리·머니마켓 ETF는 매일
    상승) 폭 지표가 위로 왜곡됐음 (2026-10-09 발견). 하루 1회 캐시, 조회 실패 시 직전 캐시.
    """
    try:
        if os.path.exists(UNIVERSE_CACHE):
            c = json.load(open(UNIVERSE_CACHE, encoding='utf-8'))
            if c.get('date') == date.today().isoformat():
                return set(c['codes'])
        import FinanceDataReader as fdr
        L = fdr.StockListing('KRX')
        codes = sorted(L[L['Market'].isin(['KOSPI', 'KOSDAQ', 'KOSDAQ GLOBAL'])]['Code'])
        os.makedirs(os.path.dirname(UNIVERSE_CACHE), exist_ok=True)
        json.dump({'date': date.today().isoformat(), 'codes': codes},
                  open(UNIVERSE_CACHE, 'w', encoding='utf-8'))
        return set(codes)
    except Exception:
        if os.path.exists(UNIVERSE_CACHE):
            return set(json.load(open(UNIVERSE_CACHE, encoding='utf-8'))['codes'])
        raise


def breadth_frame(p: date, official: dict = None, universe: set = None) -> pd.DataFrame:
    """폭 정의 v2 시계열 — 유니버스 = 패널 ∩ KRX 상장 주권 (ETF·ETN 제외).

    P일 행은 ka10066 KRX 확정 종가로 대체하고 (MDC 최종일은 15:35 잠정), P일 상승·하락은
    KRX 공식 등락률(기준가 대비)로 집계한다. P 이전 행은 MDC 확정값 (V1 표본 1,742건 0원 일치).
    A/D 누적 기준점 = 패널 첫날 (brief_config.AD_ORIGIN). MDC 파일은 읽기만 한다.
    한계: 유니버스는 현재 상장 주권 — 과거 상장폐지 종목 누락(생존편향).
    """
    from market_breadth import pct_above_ma200
    panel = pd.read_parquet(os.path.join(DATA, 'breadth_close_panel.parquet'))
    universe = universe if universe is not None else stock_universe()
    codes = [c for c in panel.columns if c in universe]
    pnl = panel[codes].copy()
    pnl.index = [d.date() for d in pd.DatetimeIndex(pnl.index)]
    pnl = pnl[[d <= p and d >= pd.Timestamp(cfg.AD_ORIGIN).date() for d in pnl.index]]
    if official is not None:
        pnl.loc[p] = pd.Series({c: official['codes'][c]['px'] if c in official['codes']
                                else np.nan for c in codes})
        pnl = pnl.sort_index()
    chg = pnl.pct_change(fill_method=None)
    up, dn = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    if official is not None:
        flu = pd.Series({c: official['codes'][c]['flu'] for c in codes if c in official['codes']})
        up.loc[p], dn.loc[p] = int((flu > 0).sum()), int((flu < 0).sum())
    out = pd.DataFrame({'up': up, 'dn': dn})
    out['ad_diff'] = out['up'] - out['dn']
    out.iloc[0, out.columns.get_loc('ad_diff')] = 0      # 기준점 당일은 변화 없음
    out['ad_cum'] = out['ad_diff'].cumsum()
    out['pct_above200'] = pct_above_ma200(pnl)
    out['universe'] = len(codes)
    return out


def breadth_confirmed(official: dict, p: date, universe: set = None) -> dict:
    """official=None이면 대체 없이 패널 그대로 (재생 시 P 행은 이미 MDC 확정값)."""
    f = breadth_frame(p, official, universe)
    last = f.iloc[-1]
    # A/D 20일 비율 = 20일 상승 종목 합 ÷ 하락 종목 합 (누적 절대값은 기준일 의존이라 표시 제외)
    ratio = f['up'].rolling(20).sum() / f['dn'].rolling(20).sum().replace(0, np.nan)
    r_last = float(ratio.iloc[-1])
    return {'pct_above200': float(last['pct_above200']), 'ad_cum': float(last['ad_cum']),
            'ad_chg20': float(last['ad_cum'] - f['ad_cum'].iloc[-21]),
            'up': int(last['up']), 'dn': int(last['dn']), 'universe': int(last['universe']),
            'ad_ratio20': r_last, 'ad_ratio20_pctile': pctile_strict(ratio, r_last),
            'ad_ratio_n': int(ratio.notna().sum()), 'date': f.index[-1]}


def market_foreign_z(official_frgn_eok: float, p: date) -> dict:
    path = os.path.join(DATA, 'market_foreign.parquet')
    if not os.path.exists(path):
        return {'z20': None, 'z60': None, 'last': None, 'n': 0}
    s = pd.read_parquet(path)['frgn_eok']
    s.index = [d.date() for d in pd.DatetimeIndex(s.index)]
    if official_frgn_eok is None:      # 재생 — P까지 ka10059 합산(확정) 그대로
        s = s[[d <= p for d in s.index]]
    else:
        s = s[[d < p for d in s.index]]
        s.loc[p] = official_frgn_eok  # 최종 세션은 ka10066 확정 합
    s = s.sort_index()
    out = {'last': float(s.iloc[-1]), 'n': int(len(s)), 'last_date': s.index[-1]}
    for w in (20, 60):
        base = s.iloc[-w - 1:-1]
        out[f'z{w}'] = (float((s.iloc[-1] - base.mean()) / base.std(ddof=1))
                        if len(base) == w and base.std(ddof=1) else None)
    return out


def weighted_z(us_closes: dict, weights: dict, closed: list, x_cum: float) -> float:
    """누적 n세션 가중 수익률의 z = (x − n·μ) / (σ·√n), μ·σ는 직전 SIGMA_WINDOW 세션 일간 가중수익."""
    if x_cum is None or not closed:
        return None
    rets = pd.DataFrame({s: us_closes[s].pct_change() * 100 for s in weights}).dropna()
    w = pd.Series(weights)
    daily = (rets[list(w.index)] * w).sum(axis=1) / w.sum()
    prior = daily[[d < closed[0] for d in daily.index]].iloc[-cfg.SIGMA_WINDOW:]
    if len(prior) < cfg.SIGMA_WINDOW:
        return None
    n = len(closed)
    return float((x_cum - n * prior.mean()) / (prior.std(ddof=1) * np.sqrt(n)))


def dca_line(e: date, trig: bool, z: float, state: dict) -> str:
    """정기 회차 여부 + 삼전 회차 당김 (해당 주기 1회)."""
    from brief.dca import cycle_of, pull_status, scheduled_exec
    sched = scheduled_exec(cycle_of(e))
    regular = '정기 회차 실행' if e == sched else f'정기 회차 없음 (다음 {sched:%m/%d})'
    zt = '' if z is None else f' (가중 z {z:+.1f})'
    if not cfg.DCA_PULL_FORWARD:
        return f'🎯 적립: {regular}'
    pull = {'none': '삼전 당김 미해당',
            'same_day': f'삼전 트리거 해당 · 정기일과 동일{zt}',
            'passed': f'삼전 트리거 해당 · 정기일 경과(당김 없음){zt}',
            'spent': f'삼전 트리거 해당 · 이번 주기 당김 소진{zt}',
            'pull': f'삼전 회차 당김 해당{zt}'}[pull_status(e, trig, state)]
    return f'🎯 적립: {regular} · {pull}'


def ecos_usdkrw(p: date):
    """한국은행 ECOS 원/달러 (서울 외환시장 15:30 종가). 키 없음·응답 없음이면 None."""
    key = os.environ.get('ECOS_KEY')
    if not key:
        return None
    import requests
    start = (p - timedelta(days=60)).strftime('%Y%m%d')
    url = (f'https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100/'
           f'{cfg.ECOS_USDKRW_STAT}/D/{start}/{p:%Y%m%d}/{cfg.ECOS_USDKRW_ITEM}')
    try:
        rows = requests.get(url, timeout=30).json()['StatisticSearch']['row']
        return pd.Series({pd.Timestamp(r['TIME']): float(r['DATA_VALUE']) for r in rows}).sort_index()
    except Exception:
        return None


async def collect(now_kst: datetime = None) -> dict:
    from modules.semi_trigger.token_provider import get_semi_token
    from macro_monitor import compute_regime, load_history
    from market_breadth import adx_atr
    from brief.expected_gap import (build_gap_history, load_kr_ohlc, load_us_closes, predict,
                                    stats_from_history)
    from modules.semi_trigger.kr_calendar import prev_kr_trading_day
    import yfinance as yf

    now_kst = now_kst or datetime.now(KST)
    sf = session_frame(now_kst)
    p = sf['prev_kr']
    token = await get_semi_token()
    d = {'frame': sf, 'now': now_kst}

    # KOSPI 국면·변동성 (ka20006 parquet)
    k = pd.read_parquet(os.path.join(DATA, 'kospi_daily_ohlc.parquet'))
    k.index = [x.date() for x in pd.DatetimeIndex(k.index)]
    k = k[[x <= p for x in k.index]]
    d['kospi_last'] = k.index[-1]
    reg = compute_regime({x.isoformat(): float(v) for x, v in k['close'].items()})
    hist = [r for r in load_history() if r['date'] < p.strftime('%Y%m%d')]
    dur = 1
    for r in reversed(hist):
        if (r.get('regime') or {}).get('label') == reg['label']:
            dur += 1
        else:
            break
    d['regime'] = {**reg, 'dur': dur}
    ax = adx_atr(k)
    atr_s = (ax['atr'] / k['close'] * 100).dropna()
    d['atr'] = {'pct': float(atr_s.iloc[-1]), 'pctile': pctile_strict(atr_s, float(atr_s.iloc[-1]))}

    # 폭 + 외인 시장 (ka10066 확정)
    univ = stock_universe()
    # ka10066은 항상 '실제 최신 세션' 확정값 — 과거 시점 재생(골든 테스트)에서 P가 최신 세션이
    # 아니면 대체하지 않는다 (P 이전 MDC 행·ka10059 합산은 이미 확정값)
    live_p = session_frame(datetime.now(KST))['prev_kr']
    off = await ka10066_official(token, univ) if p == live_p else None
    d['replay'] = p != live_p
    d['breadth'] = breadth_confirmed(off, p, univ)
    d['foreign'] = market_foreign_z(off['kospi_frgn_eok'] if off else None, p)

    # 미10Y·원달러
    tnx = yf.Ticker('^TNX').history(period='3mo', interval='1d')['Close']
    krw, krw_src = ecos_usdkrw(p), 'ecos'
    if krw is None:
        krw, krw_src = yf.Ticker('KRW=X').history(period='3mo', interval='1d')['Close'], 'yfinance'
    for name, s, cut, src in (('us10y', tnx, sf['d_last'], 'yfinance'),
                              ('usdkrw', krw, p if krw_src == 'ecos' else sf['d_last'], krw_src)):
        idx = pd.DatetimeIndex(s.index)
        s.index = [x.date() for x in (idx.tz_localize(None) if idx.tz is not None else idx)]
        s = s[[x <= cut for x in s.index]]
        d[name] = {'last': float(s.iloc[-1]), 'chg20': float(s.iloc[-1] - s.iloc[-21]),
                   'chg20_pct': float((s.iloc[-1] / s.iloc[-21] - 1) * 100), 'date': s.index[-1],
                   'source': src}

    # 반도체 블록
    us = load_us_closes()
    us = {s: v[[x <= sf['d_last'] for x in v.index]] for s, v in us.items()}
    kr = await load_kr_ohlc(token)
    kr = {c: v[[x <= p for x in v.index]] for c, v in kr.items()}
    gh = build_gap_history(us, kr)
    pred = predict(sf['exec'], us, gh, prev_kr_trading_day)
    st = stats_from_history(gh)
    d['semi'] = {}
    for code in ('005930', '000660'):
        v = pred['by_code'][code]
        z = weighted_z(us, cfg.STOCK_US_WEIGHTS[code], pred['sessions'], v['x'])
        c = kr[code]['close']
        fl = await stock_flows_20d(token, code)
        fl = fl[[x <= p for x in fl.index]]
        d['semi'][code] = {
            'x': v['x'], 'z': z, 'exp_gap': v['exp_gap'], 'beta': v['beta'],
            'beta_max_date': v.get('beta_max_date'),
            'mae': st.get(code, {}).get('mae'),
            'ma60': float((c.iloc[-1] / c.iloc[-60:].mean() - 1) * 100),
            'ma200': float((c.iloc[-1] / c.iloc[-200:].mean() - 1) * 100),
            'frgn20': float(fl.iloc[-20:].sum()), 'frgn_last': fl.index[-1] if len(fl) else None,
            'kr_last': c.index[-1]}
    d['sessions_used'] = pred['sessions']
    sam = d['semi']['005930']
    d['trigger'] = bool((sam['z'] is not None and sam['z'] <= cfg.Z_TH)
                        or (sam['x'] is not None and sam['x'] <= cfg.RET_TH))
    return d


# ── 렌더 ─────────────────────────────────────────────────────
def render(d: dict, state: dict = None, failures: list = None) -> str:
    sf, r, b, f = d['frame'], d['regime'], d['breadth'], d['foreign']
    sam, hyn = d['semi']['005930'], d['semi']['000660']
    p = sf['prev_kr']
    kind = '확정' if sf['final'] else '예비'
    lines = [
        f"📊 [일일 브리프] US {sf['d_last']:%m/%d} 세션 → KR 실행 {sf['exec']:%m/%d} "
        f"({kind} · 미국 {len(sf['closed'])}/{sf['total']}세션 반영)",
        dca_line(sf['exec'], d['trigger'], sam['z'], state or {}),
        '',
        '━ 시장 국면 (KOSPI) ━',
        f"국면 {r['label']} {r['dur']}일 · 고점 대비 {pct(r['dd'])} · 3개월 {pct(r['mom'])}"
        + lag_tag(d['kospi_last'], p),
    ]
    zs = [f.get('z20'), f.get('z60')]
    ext = ' (극단)' if any(z is not None and abs(z) >= cfg.FOREIGN_Z_EXTREME for z in zs) else ''
    fz = lambda z: 'N/A' if z is None else f'{z:+.1f}'  # noqa: E731
    lines.append(f"외인 z20 {fz(zs[0])} / z60 {fz(zs[1])}{ext} · 외국인(기타외국인 제외)"
                 + (lag_tag(f.get('last_date'), p) if f.get('n') else ' (결측)'))
    lines.append(f"폭 200일선 위 {b['pct_above200']:.0f}% · A/D 20일 비율 {b['ad_ratio20']:.2f} "
                 f"({b['ad_ratio20_pctile']:.0f}%ile)")
    u, x = d['us10y'], d['usdkrw']
    lines.append(f"미10Y {u['last']:.2f}% (20일 {u['chg20'] * 100:+.0f}bp) · "
                 f"원달러 {x['last']:,.0f} (20일 {x['chg20_pct']:+.1f}%)"
                 + ('' if x.get('source') == 'ecos' else ' ⚠️ 미검증'))
    hv = ', 고변동' if d['atr']['pctile'] >= 80 else ''
    lines.append(f"변동성 ATR14 {d['atr']['pct']:.1f}% ({d['atr']['pctile']:.0f}%ile{hv})")
    zf = lambda z: 'N/A' if z is None else f'{z:+.1f}'  # noqa: E731
    lines += [
        '',
        '━ 반도체 블록 ━',
        f"전야 MU/SNDK 가중 삼전 {pct(sam['x'])} · 하닉 {pct(hyn['x'])} "
        f"(z {zf(sam['z'])}/{zf(hyn['z'])})",
        f"예상 갭 삼전 {pct(sam['exp_gap'])} · 하닉 {pct(hyn['exp_gap'])} "
        f"(MAE {sam['mae']:.1f}/{hyn['mae']:.1f}%p)",
    ]
    for nm, s in (('삼전', sam), ('하닉', hyn)):
        lines.append(f"{nm} MA60 {pct(s['ma60'])} · MA200 {pct(s['ma200'])} · "
                     f"외인 20일 {money_eok(s['frgn20'])}"
                     + lag_tag(s['frgn_last'], p))
    lines.append(f"반도체 외인 20일 {money_eok(sam['frgn20'] + hyn['frgn20'])}")
    if failures:
        lines += ['', '⚠️ 검증: ' + ' / '.join(failures)]
    return '\n'.join(lines)


async def _cli():
    from brief.dca import load_state
    d = await collect()
    print(render(d, load_state()))


if __name__ == '__main__':
    asyncio.run(_cli())
