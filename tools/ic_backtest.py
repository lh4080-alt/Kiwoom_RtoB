# -*- coding: utf-8 -*-
"""③ IC 검증 — 거시 지표의 코스피 미래 수익률 예측력 (표시 전용 지표의 승격 관문).

방법 (사전 선언 2026-10-06 — 결과 본 뒤 규칙 바꾸지 않음):
  지표(t일 값) ↔ 미래 수익률 r_h(t) = 종가 t→t+h, Spearman 랭크상관(IC)
  통과 규칙: |IC| >= 0.05  AND  |t| >= 2 (겹침 보정: 유효 표본 n/h)  AND
            전반/후반 절반 분할에서 부호 일치
  통과 실패 지표는 표시 전용 유지 → 방향 점수화(다음 단계) 대상에서 제외.

데이터:
  - ka20006 코스피 지수 OHLC 600봉 (실행 시 config/data/kospi_daily_ohlc.parquet 갱신)
  - config/data/breadth_close_panel.parquet (MDC 4,392종목) — breadth 지표군

실행: beelink에서 python tools/ic_backtest.py
"""
import asyncio
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))

OHLC_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
PANEL_PATH = os.path.join(BASE, '..', 'config', 'data', 'breadth_close_panel.parquet')

HORIZONS = (1, 5, 10)
IC_MIN = 0.05
T_MIN = 2.0


async def fetch_kospi_ohlc_600() -> pd.DataFrame:
    """ka20006 코스피 지수 OHLC 600봉 (운영 fetch_kospi_index_closes의 OHLC 확장)."""
    from modules.semi_trigger.token_provider import get_semi_token
    from utils.rate_limiter import requests
    import utils.config as config
    token = await get_semi_token()
    rows, cont, nk = [], 'N', ''
    for _page in range(6):
        r = await requests.post(
            config.get_host_url() + '/api/dostk/chart',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {token}', 'cont-yn': cont,
                     'next-key': nk, 'api-id': 'ka20006'},
            json={'inds_cd': '001', 'base_dt': datetime.now().strftime('%Y%m%d')})
        d = r.json()

        def _v(key, it):
            s = str(it.get(key, '')).strip().lstrip('-')
            return int(s) / 100.0 if s else np.nan  # 지수는 소수점 제거 100배

        for it in d.get('inds_dt_pole_qry') or []:
            dt = str(it.get('dt', ''))
            if len(dt) == 8:
                rows.append({'dt': pd.Timestamp(dt), 'open': _v('open_pric', it),
                             'high': _v('high_pric', it), 'low': _v('low_pric', it),
                             'close': _v('cur_prc', it)})
        cont = r.headers.get('cont-yn', 'N')
        nk = r.headers.get('next-key', '')
        if cont != 'Y':
            break
    return pd.DataFrame(rows).drop_duplicates('dt').set_index('dt').sort_index()


def index_indicators(ohlc: pd.DataFrame, adx_atr_fn) -> pd.DataFrame:
    """지수 레벨 지표군 — 운영 macro_monitor/compute_regime·short_term과 동일 정의."""
    c = ohlc['close']
    out = pd.DataFrame(index=ohlc.index)
    ma200 = c.rolling(200).mean()
    out['trend_200disp'] = (c / ma200 - 1) * 100          # 200일선 이격도
    out['dd_250'] = (c / c.rolling(250).max() - 1) * 100  # 52주 고점대비
    out['mom_63'] = c.pct_change(63) * 100                # 3개월 모멘텀
    r5 = c.pct_change(5) * 100
    out['ret5'] = r5
    out['z5'] = (r5 - r5.rolling(60).mean()) / r5.rolling(60).std()
    ma5, ma20, ma60 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(60).mean()
    out['ma5v20'] = (ma5 > ma20).astype(float).where(ma5.notna() & ma20.notna())
    out['ma20v60'] = (ma20 > ma60).astype(float).where(ma20.notna() & ma60.notna())
    aa = adx_atr_fn(ohlc)                                  # market_breadth.adx_atr 재사용
    out['adx14'] = aa['adx']
    atr_pct = aa['atr'] / c * 100
    out['atr_pctile60'] = atr_pct.rolling(60).rank(pct=True) * 100
    return out


def breadth_indicators(panel: pd.DataFrame, ad_line_fn, pct_above_fn) -> pd.DataFrame:
    """전종목 breadth 지표군 — market_breadth 함수 재사용."""
    out = pd.DataFrame(index=panel.index)
    line, diff, up, dn = ad_line_fn(panel)
    out['ad_diff20'] = line.diff(20)                       # A/D 라인 20일 변화
    p200 = pct_above_fn(panel)
    out['pct_above200'] = p200                             # 200일선 위 종목 비율
    out['pct_above200_chg10'] = p200.diff(10)              # 그 10일 변화
    return out


def forward_returns(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({f'r{h}': (close.shift(-h) / close - 1) * 100 for h in HORIZONS},
                        index=close.index)


def ic_stats(ind: pd.Series, fwd: pd.DataFrame) -> dict:
    """지표 1개 × 전 horizon IC. t는 겹침 보정(유효 표본 n/h) 근사."""
    res = {}
    for h in HORIZONS:
        col = f'r{h}'
        df = pd.concat([ind.rename('x'), fwd[col]], axis=1).dropna()
        n = len(df)
        if n < 60:
            res[h] = None
            continue
        ic = df['x'].corr(df[col], method='spearman')
        n_eff = max(n // h, 10)
        t = ic * np.sqrt((n_eff - 2) / max(1 - ic * ic, 1e-9))
        half = n // 2
        ic1 = df.iloc[:half]['x'].corr(df.iloc[:half][col], method='spearman')
        ic2 = df.iloc[half:]['x'].corr(df.iloc[half:][col], method='spearman')
        res[h] = {'ic': round(ic, 3), 't': round(t, 2), 'n': n,
                  'ic_h1': round(ic1, 3), 'ic_h2': round(ic2, 3),
                  'pass': bool(abs(ic) >= IC_MIN and abs(t) >= T_MIN
                               and np.sign(ic1) == np.sign(ic2) != 0)}
    return res


async def main() -> int:
    from market_breadth import ad_line as ad_line_fn, adx_atr as adx_atr_fn, pct_above_ma200 as pct_above_fn

    print('[ic] ka20006 OHLC 600봉 조회...')
    ohlc = await fetch_kospi_ohlc_600()
    if len(ohlc) < 320:
        print(f'[ic] OHLC 봉 부족: {len(ohlc)}')
        return 1
    ohlc.to_parquet(OHLC_PATH)  # market_breadth 동일 형식 — PanelBuild와 상호 호환
    print(f'[ic] OHLC {len(ohlc)}봉 ({ohlc.index.min().date()}~{ohlc.index.max().date()}) 갱신 저장')

    fwd = forward_returns(ohlc['close'])
    results = {}

    idx_ind = index_indicators(ohlc, adx_atr_fn)
    for name in idx_ind.columns:
        results[f'[지수] {name}'] = ic_stats(idx_ind[name], fwd)

    if os.path.exists(PANEL_PATH):
        panel = pd.read_parquet(PANEL_PATH)
        b_ind = breadth_indicators(panel, ad_line_fn, pct_above_fn)
        for name in b_ind.columns:
            results[f'[breadth] {name}'] = ic_stats(b_ind[name], fwd)
    else:
        print('[ic] 패널 없음 — breadth 지표군 스킵')

    # 리포트
    print()
    print(f'{"지표":<28}{"h":>3}{"IC":>7}{"t(보정)":>8}{"n":>6}{"전반":>7}{"후반":>7}  판정')
    print('-' * 80)
    n_pass = 0
    for name, hs in results.items():
        for h, s in hs.items():
            if s is None:
                continue
            mark = '★ 통과' if s['pass'] else ''
            if s['pass']:
                n_pass += 1
            print(f'{name:<28}{h:>3}{s["ic"]:>+7.3f}{s["t"]:>8.2f}{s["n"]:>6}'
                  f'{s["ic_h1"]:>+7.3f}{s["ic_h2"]:>+7.3f}  {mark}')
    print('-' * 80)
    print(f'통과: {n_pass}개 / 전체 {sum(len([s for s in hs.values() if s]) for hs in results.values())}셀')

    import json
    with open(os.path.join(BASE, 'ic_results.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print('[ic] 저장: tools/ic_results.json')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
