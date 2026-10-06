# -*- coding: utf-8 -*-
"""③ IC 검증 — 거시 지표의 코스피 미래 수익률 예측력 (표시 전용 지표의 승격 관문).

방법 (사전 선언 2026-10-06, Lee 지시 반영 v2):
  지표(t일 값) ↔ 미래 수익률 r_h(t), Spearman 랭크상관(IC)
  1차 관문: |IC| >= 0.05 & |t| >= 2 (겹침 보정) & 전반/후반 부호 일치 → "후보(참고)"
  다중비교 보정: 전체 셀 기준 Bonferroni t >= 3.2 → "유의"
  보조 분석:
    - ADX 방향 분해 (adx_signed = ADX × sign(+DI-−DI), 상승/하락 추세 분리)
    - |수익률| 타깃 (변동성 예측력)
    - 5분위 + 극단 구간 조건부 수익률 (선형 IC가 못 보는 비선형 구간)

데이터:
  - ka20006 코스피 지수 OHLC (~3600봉, 2012~) → config/data/kospi_daily_ohlc.parquet 갱신
  - config/data/breadth_close_panel.parquet (MDC 4,392종목) — breadth 지표군

실행: beelink에서 python tools/ic_backtest.py
"""
import asyncio
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')  # Pandas4Warning 스팸 억제 (concat 정렬 기본값 통보)

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))

OHLC_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
PANEL_PATH = os.path.join(BASE, '..', 'config', 'data', 'breadth_close_panel.parquet')
FLOWS_PATH = os.path.join(BASE, '..', 'config', 'data', 'investor_flows.parquet')
FULL_PANEL_PATH = os.path.join(BASE, '..', 'config', 'data', 'breadth_panel_full.parquet')

HORIZONS = (1, 5, 10)
IC_MIN = 0.05
T_MIN = 2.0
T_BONF = 3.2  # 다중비교 보정 (36+셀 Bonferroni 근사)


async def fetch_kospi_ohlc_600() -> pd.DataFrame:
    """ka20006 코스피 지수 OHLC (페이지당 600봉, 6페이지 — 2012년대까지 확보됨)."""
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


def di_columns(ohlc: pd.DataFrame, n: int = 14):
    """Wilder +DI/−DI (ADX 방향 분해용)."""
    high, low, close = ohlc['high'], ohlc['low'], ohlc['close']
    up, dn = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr.replace(0, np.nan)
    return plus_di, minus_di


def index_indicators(ohlc: pd.DataFrame, adx_atr_fn) -> pd.DataFrame:
    """지수 레벨 지표군 — 운영 macro_monitor 정의 + ADX 방향 분해 (v2)."""
    c = ohlc['close']
    out = pd.DataFrame(index=ohlc.index)
    ma200 = c.rolling(200).mean()
    out['trend_200disp'] = (c / ma200 - 1) * 100
    out['dd_250'] = (c / c.rolling(250).max() - 1) * 100
    out['mom_63'] = c.pct_change(63) * 100
    r5 = c.pct_change(5) * 100
    out['ret5'] = r5
    out['z5'] = (r5 - r5.rolling(60).mean()) / r5.rolling(60).std()
    ma5, ma20, ma60 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(60).mean()
    out['ma5v20'] = (ma5 > ma20).astype(float).where(ma5.notna() & ma20.notna())
    out['ma20v60'] = (ma20 > ma60).astype(float).where(ma20.notna() & ma60.notna())
    aa = adx_atr_fn(ohlc)
    out['adx14'] = aa['adx']
    atr_pct = aa['atr'] / c * 100
    out['atr_pctile60'] = atr_pct.rolling(60).rank(pct=True) * 100
    # ADX 방향 분해 (Lee v2 지시) — 부호화/상승추세/하락추세 분리
    plus_di, minus_di = di_columns(ohlc)
    di_dir = np.sign(plus_di - minus_di)
    out['adx_signed'] = aa['adx'] * di_dir
    out['adx_up'] = aa['adx'].where(di_dir > 0)   # 상승 추세 중 ADX
    out['adx_dn'] = aa['adx'].where(di_dir < 0)   # 하락 추세 중 ADX
    return out


def breadth_indicators(panel: pd.DataFrame, ad_line_fn, pct_above_fn) -> pd.DataFrame:
    out = pd.DataFrame(index=panel.index)
    line, diff, up, dn = ad_line_fn(panel)
    out['ad_diff20'] = line.diff(20)
    p200 = pct_above_fn(panel)
    out['pct_above200'] = p200
    out['pct_above200_chg10'] = p200.diff(10)
    return out


def forward_returns(close: pd.Series) -> dict:
    """부호 수익률 + 절대수익률(변동성 예측력 타깃) — v2."""
    out = {}
    for h in HORIZONS:
        r = (close.shift(-h) / close - 1) * 100
        out[f'r{h}'] = r
        out[f'a{h}'] = r.abs()
    return out


def ic_stats(ind: pd.Series, targets: dict, keys: list) -> dict:
    res = {}
    for key in keys:
        col = targets[key]
        df = pd.concat([ind.rename('x'), col.rename('y')], axis=1).dropna()
        n = len(df)
        if n < 60:
            res[key] = None
            continue
        h = int(key[1:])
        ic = df['x'].corr(df['y'], method='spearman')
        n_eff = max(n // h, 10)
        t = ic * np.sqrt((n_eff - 2) / max(1 - ic * ic, 1e-9))
        half = n // 2
        ic1 = df.iloc[:half]['x'].corr(df.iloc[:half]['y'], method='spearman')
        ic2 = df.iloc[half:]['x'].corr(df.iloc[half:]['y'], method='spearman')
        res[key] = {'ic': round(ic, 3), 't': round(t, 2), 'n': n,
                    'ic_h1': round(ic1, 3), 'ic_h2': round(ic2, 3),
                    'cand': bool(abs(ic) >= IC_MIN and abs(t) >= T_MIN
                                 and np.sign(ic1) == np.sign(ic2) != 0),
                    'bonf': bool(abs(t) >= T_BONF)}
    return res


def partial_ic(x: pd.Series, y: pd.Series, z: pd.Series) -> dict:
    """통제변수 z의 순위 편상관 — 실현변동성 통제 후 증분 정보 검증 (v2 지시 1번)."""
    df = pd.concat([x.rename('x'), y.rename('y'), z.rename('z')], axis=1).dropna()
    n = len(df)
    if n < 200:
        return None
    r_xy = df['x'].corr(df['y'], method='spearman')
    r_xz = df['x'].corr(df['z'], method='spearman')
    r_yz = df['y'].corr(df['z'], method='spearman')
    denom = np.sqrt(max(1 - r_xz ** 2, 1e-9) * max(1 - r_yz ** 2, 1e-9))
    pic = (r_xy - r_xz * r_yz) / denom
    n_eff = max(n // 5, 10)
    t = pic * np.sqrt((n_eff - 2) / max(1 - pic * pic, 1e-9))
    return {'pic': round(pic, 3), 't': round(t, 2), 'n': n}


def quintile_report(ind: pd.Series, targets: dict, q: int = 5) -> pd.DataFrame:
    """5분위 조건부 평균 수익률 (선형 IC 보완, v2)."""
    rows = []
    for key in ('r5', 'r10'):
        df = pd.concat([ind.rename('x'), targets[key].rename('y')], axis=1).dropna()
        if len(df) < 100:
            continue
        try:
            qs = pd.qcut(df['x'], q, labels=False, duplicates='drop')
        except ValueError:
            continue
        g = df.groupby(qs)['y'].agg(['mean', 'count'])
        for qi, row in g.iterrows():
            rows.append({'bucket': f'Q{qi + 1}', 'key': key,
                         'n': int(row['count']), 'mean_r': round(row['mean'], 2)})
    return pd.DataFrame(rows)


def flow_indicators(flows: pd.DataFrame) -> pd.DataFrame:
    """투자자 수급 지표군 (ka10051 백필, 2021~) — 억원."""
    out = pd.DataFrame(index=flows.index)
    f, o, i_ = flows['frgnr_eok'], flows['orgn_eok'], flows['ind_eok']
    out['frgnr_net'] = f
    out['frgnr_cum20'] = f.rolling(20).sum()
    out['orgn_net'] = o
    out['orgn_cum20'] = o.rolling(20).sum()
    out['ind_net'] = i_                      # 개인 — 반대 부호 예상
    out['frgnr_minus_orgn'] = f - o          # 수급 괴리
    if 'ee_frgnr_eok' in flows.columns:      # 전기/전자(반도체 블록 근사)
        ef = flows['ee_frgnr_eok']
        out['ee_frgnr_net'] = ef
        out['ee_frgnr_cum20'] = ef.rolling(20).sum()
    return out


async def macro_indicators_15y() -> pd.DataFrame:
    """달러·미10Y 지표군 (yfinance ~15년) — 운영 fetch_macro_histories 재사용."""
    from macro_monitor import fetch_macro_histories, SYM_USDKRW, SYM_US10Y
    macro = await asyncio.to_thread(fetch_macro_histories, 15.0)
    out = {}
    usd = macro.get(SYM_USDKRW, {})
    if usd:
        s = pd.Series(usd).sort_index()
        s.index = pd.to_datetime(s.index)
        out['usd_chg5'] = (s / s.shift(5) - 1) * 100
    us10y = macro.get(SYM_US10Y, {})
    if us10y:
        s = pd.Series(us10y).sort_index()
        s.index = pd.to_datetime(s.index)
        out['us10y_chg20'] = (s - s.shift(20)) * 100  # bp
    return pd.DataFrame(out)


async def main() -> int:
    from market_breadth import ad_line as ad_line_fn, adx_atr as adx_atr_fn, pct_above_ma200 as pct_above_fn

    print('[ic] ka20006 OHLC 조회...')
    ohlc = await fetch_kospi_ohlc_600()
    if len(ohlc) < 320:
        print(f'[ic] OHLC 봉 부족: {len(ohlc)}')
        return 1
    ohlc.to_parquet(OHLC_PATH)
    print(f'[ic] OHLC {len(ohlc)}봉 ({ohlc.index.min().date()}~{ohlc.index.max().date()}) 갱신 저장')

    targets = forward_returns(ohlc['close'])
    r_keys = [f'r{h}' for h in HORIZONS]
    a_keys = [f'a{h}' for h in HORIZONS]

    idx_ind = index_indicators(ohlc, adx_atr_fn)
    all_ind = {f'[지수] {name}': idx_ind[name] for name in idx_ind.columns}

    if os.path.exists(PANEL_PATH):
        panel = pd.read_parquet(PANEL_PATH)
        b_ind = breadth_indicators(panel, ad_line_fn, pct_above_fn)
        for name in b_ind.columns:
            all_ind[f'[breadth] {name}'] = b_ind[name]
    else:
        print('[ic] 패널 없음 — breadth 지표군 스킵')

    # Phase 2 (v3): breadth 풀 패널이 있으면 그쪽 사용 (2021~)
    if os.path.exists(FULL_PANEL_PATH):
        full_panel = pd.read_parquet(FULL_PANEL_PATH)
        b_full = breadth_indicators(full_panel, ad_line_fn, pct_above_fn)
        for name in b_full.columns:
            all_ind[f'[breadth21~] {name}'] = b_full[name]
        print(f'[ic] 풀 패널 breadth: {full_panel.shape} ({full_panel.index.min().date()}~)')
        # 운영 패널 버전은 풀 패널에 포함되므로 중복 제거
        for k in [k for k in list(all_ind) if k.startswith('[breadth] ')]:
            del all_ind[k]

    if os.path.exists(FLOWS_PATH):
        flows = pd.read_parquet(FLOWS_PATH)
        flows.index = pd.to_datetime(flows.index, format='%Y%m%d')
        f_ind = flow_indicators(flows)
        for name in f_ind.columns:
            all_ind[f'[수급] {name}'] = f_ind[name]
        print(f'[ic] 투자자 수급: {len(flows)}일')
    else:
        print('[ic] 투자자 수급 없음 — 스킵')

    try:
        m_ind = await macro_indicators_15y()
        for name in m_ind.columns:
            all_ind[f'[거시] {name}'] = m_ind[name]
        print(f'[ic] 거시(달러·미10Y): {len(m_ind.columns)}지표')
    except Exception as e:
        print(f'[ic] 거시 지표 실패: {e}')

    # ── 1) 부호 수익률 IC (후보/유의 구분, v2) ──────────────────
    print()
    print(f'== 1) 부호 수익률 IC (후보=|IC|>=.05&t>=2&부호일치 / 유의=t>={T_BONF}) ==')
    print(f'{"지표":<30}{"h":>3}{"IC":>7}{"t":>7}{"n":>6}{"전반":>7}{"후반":>7}  판정')
    print('-' * 82)
    for name, ind in all_ind.items():
        for h, s in ic_stats(ind, targets, r_keys).items():
            if s is None:
                continue
            mark = '★유의' if s['bonf'] else ('○후보' if s['cand'] else '')
            print(f'{name:<30}{h:>3}{s["ic"]:>+7.3f}{s["t"]:>7.2f}{s["n"]:>6}'
                  f'{s["ic_h1"]:>+7.3f}{s["ic_h2"]:>+7.3f}  {mark}')

    # ── 2) |수익률| 타깃 (변동성 예측력) ────────────────────────
    print()
    print('== 2) |수익률| 타깃 IC (변동성 예측력) ==')
    print(f'{"지표":<30}{"h":>3}{"IC":>7}{"t":>7}{"n":>6}  판정')
    print('-' * 70)
    for name, ind in all_ind.items():
        for h, s in ic_stats(ind, targets, a_keys).items():
            if s is None:
                continue
            mark = '★유의' if s['bonf'] else ('○후보' if s['cand'] else '')
            print(f'{name:<30}{h:>3}{s["ic"]:>+7.3f}{s["t"]:>7.2f}{s["n"]:>6}  {mark}')

    # ── 3) 5분위 + 극단 구간 조건부 수익률 ──────────────────────
    print()
    print('== 3) 5분위 조건부 수익률 (Q1=지표 최저 ~ Q5=최고, 평균 r) ==')
    focus = ['adx14', 'adx_dn', 'dd_250', 'z5', 'atr_pctile60',
             'frgnr_net', 'frgnr_cum20', 'ind_net', 'ee_frgnr_net']
    for name in focus:
        for full in [k for k in all_ind if k.endswith(name)]:
            qt = quintile_report(all_ind[full], targets)
            if qt.empty:
                continue
            print(f'-- {full} --')
            for key in ('r5', 'r10'):
                sub = qt[qt['key'] == key]
                if not sub.empty:
                    cells = ' | '.join(f"{row['bucket']}:{row['mean_r']:+.2f}%(n={row['n']})"
                                       for _, row in sub.iterrows())
                    print(f'   {key}: {cells}')

    # 극단 구간 (수동 정의)
    print()
    print('== 극단 구간 조건부 (평균 r5 / r10) ==')
    c = ohlc['close']
    extremes = {
        '낙폭 dd<=-20%': idx_ind['dd_250'] <= -20,
        '낙폭 dd<=-25%': idx_ind['dd_250'] <= -25,
        '급락 z5<=-1.5': idx_ind['z5'] <= -1.5,
        '고변동 atr>=80%ile': idx_ind['atr_pctile60'] >= 80,
        '강추세 adx>=25': idx_ind['adx14'] >= 25,
    }
    for name, mask in extremes.items():
        df = pd.concat([mask.rename('m')] + [targets[k].rename(k) for k in ('r5', 'r10')],
                       axis=1).dropna()
        sel = df[df['m']]
        if len(sel) < 20:
            print(f'{name}: n={len(sel)} 표본 부족')
            continue
        print(f'{name}: n={len(sel)} | r5 평균 {sel["r5"].mean():+.2f}% '
              f'(중위수 {sel["r5"].median():+.2f}) | r10 평균 {sel["r10"].mean():+.2f}% '
              f'(중위수 {sel["r10"].median():+.2f})')

    # ── 4) 증분 변동성 — 실현변동성 통제 후 잔여 예측력 (v2 지시 1번) ──
    print()
    print('== 4) 증분 변동성 (통제: 20일 실현변동성 평균|일간|, 편상관) ==')
    print('   → 통제 후에도 |편IC|>=.05 & |t|>=2면 "ATR만으로 충분" 아님')
    c = ohlc['close']
    rv20 = c.pct_change().abs().rolling(20).mean() * 100
    for name in ('atr_pctile60', 'adx14', 'orgn_cum20', 'usd_chg5', 'frgnr_minus_orgn'):
        for full in [k for k in all_ind if k.endswith(name)]:
            for tk in ('a1', 'a5'):
                s = partial_ic(all_ind[full], targets[tk], rv20)
                if not s:
                    continue
                mark = '★ 잔여 유의' if abs(s['t']) >= 2 and abs(s['pic']) >= IC_MIN else ''
                print(f'{full:<32}{tk:>3}  편IC {s["pic"]:+.3f}  t {s["t"]:>6.2f}  n={s["n"]}  {mark}')

    # ── 5) adx_dn 다중비교 판정 병기 + 결합 조건부 (v2 지시 3번) ──
    print()
    from statistics import NormalDist

    def z_bonf(k):
        return -NormalDist().inv_cdf(0.05 / (2 * k))

    cells_dir = sum(3 for _ in all_ind)  # 부호 방향 셀 수
    t_fam, t_all = z_bonf(cells_dir), z_bonf(cells_dir * 2)
    print(f'== 5) 다중비교 판정: 방향 셀 {cells_dir}개 → Bonferroni t*={t_fam:.2f} '
          f'| 전체(|r| 포함) {cells_dir * 2}셀 → t*={t_all:.2f} ==')
    for s in (ic_stats(all_ind['[지수] adx_dn'], targets, r_keys).get('r1'),
              ic_stats(all_ind['[지수] adx_dn'], targets, r_keys).get('r5'),
              ic_stats(all_ind['[지수] adx_dn'], targets, r_keys).get('r10')):
        if s:
            v1 = '유의' if abs(s['t']) >= t_fam else '미달'
            v2 = '유의' if abs(s['t']) >= t_all else '미달'
            print(f"adx_dn {s['n']} 기준 t={s['t']:.2f} → 방향가족 {v1} / 전체 {v2}")

    print()
    print('-- adx_dn 결합 조건부 (평균 수익률) --')
    mask_dn = idx_ind['adx_dn'].notna()
    c = ohlc['close']
    for label, m in (
            ('하락추세 전체', mask_dn),
            ('하락추세+ADX>=20 (추세 강함)', mask_dn & (idx_ind['adx14'] >= 20)),
            ('하락추세+ADX>=20+dd<=-20% (낙폭 결합)',
             mask_dn & (idx_ind['adx14'] >= 20) & (idx_ind['dd_250'] <= -20))):
        df = pd.concat([m.rename('m')] + [targets[k].rename(k) for k in ('r1', 'r5', 'r10')],
                       axis=1).dropna()
        sel = df[df['m']]
        if len(sel) < 20:
            print(f'{label}: n={len(sel)} 표본 부족')
            continue
        print(f'{label}: n={len(sel)} | r1 {sel["r1"].mean():+.2f}% | r5 {sel["r5"].mean():+.2f}% '
              f'| r10 {sel["r10"].mean():+.2f}%')

    print()
    print('[ic] 저장: tools/ic_results.json (1) 부호 IC 전체)')
    import json
    dump = {name: ic_stats(ind, targets, r_keys) for name, ind in all_ind.items()}
    with open(os.path.join(BASE, 'ic_results.json'), 'w', encoding='utf-8') as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
