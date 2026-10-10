# -*- coding: utf-8 -*-
"""3자 수급 집계·패턴 — Phase 5 사전 선언 v1 (Docs/phase5_수급패턴_사전선언.md) 구현.

원천: config/data/flows3/<code>.parquet (ka10059 확정치, 백만원) — 열 frgn·natfor·orgn·ind·etc_corp·tv
대상: market(KOSPI 주권 합) / block(005930+000660 합) / 005930 / 000660
"""
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'config', 'data')
FLOWS_DIR = os.path.join(DATA, 'flows3')
COLS = ['frgn', 'natfor', 'orgn', 'ind', 'etc_corp', 'tv']
ACTORS = ['frgn', 'orgn', 'ind']
PATTERN_NAMES = {
    '++-': '외인·기관 동반 매수형', '+--': '외인 단독 매수형', '+-+': '외인·개인 매수형',
    '-++': '기관 방어형', '--+': '개인 단독 흡수형', '-+-': '기관 단독 흡수형',
    '+++': '3자 동반 매수형', '---': '3자 동반 매도형',
}
SEMI = ('005930', '000660')


MARKET_K51 = os.path.join(DATA, 'market_flows_k51.parquet')


def load_market() -> pd.DataFrame:
    """시장 합계 — ka10051 001(억원) → 백만원, 거래대금 ka20006(백만원). 사전 선언 v1.1."""
    m = pd.read_parquet(MARKET_K51)
    out = pd.DataFrame(index=pd.DatetimeIndex(m.index))
    for c in ('frgn', 'natfor', 'orgn', 'ind', 'etc_corp'):
        out[c] = m[c].values * 100
    out['tv'] = m['tv_mil'].values
    return out.sort_index()


def load_aggregates(codes: list = None) -> dict:
    """{'market','block','005930','000660','market_sum'} → DataFrame(index=date, cols=COLS).

    market = 시장 단위 원천 (load_market). market_sum = 현재 상장 주권 합산 — 생존편향이 있어 대조용만.
    """
    files = sorted(f for f in os.listdir(FLOWS_DIR) if f.endswith('.parquet'))
    if codes is not None:
        files = [f for f in files if f[:-8] in codes]
    total, per = None, {}
    for f in files:
        df = pd.read_parquet(os.path.join(FLOWS_DIR, f))[COLS]
        code = f[:-8]
        if code in SEMI:
            per[code] = df
        total = df if total is None else total.add(df, fill_value=0)
    out = {'market': load_market(), 'market_sum': total.sort_index()}
    if all(c in per for c in SEMI):
        out['block'] = per[SEMI[0]].add(per[SEMI[1]], fill_value=0).sort_index()
        for c in SEMI:
            out[c] = per[c].sort_index()
    return out


def zprior(s: pd.Series, w: int = 60) -> pd.Series:
    """z = (v − mean(직전 w)) / std(직전 w, ddof=1), 당일 미포함."""
    return (s - s.rolling(w).mean().shift(1)) / s.rolling(w).std().shift(1)


def features(agg: pd.DataFrame) -> pd.DataFrame:
    """일간·20일 누적 비율, z60, 부호 패턴, 강도, 흡수율."""
    f = pd.DataFrame(index=agg.index)
    tv20 = agg['tv'].rolling(20).sum()
    for a in ACTORS + ['etc_corp']:
        f[f'{a}_r'] = agg[a] / agg['tv']
        f[f'{a}_c20'] = agg[a].rolling(20).sum() / tv20
        f[f'{a}_r_z'] = zprior(f[f'{a}_r'])
        f[f'{a}_c20_z'] = zprior(f[f'{a}_c20'])
        f[f'{a}_c20_amt'] = agg[a].rolling(20).sum()
    for kind, suf in (('daily', '_r'), ('cum20', '_c20')):
        f[f'pat_{kind}'] = [
            ''.join('+' if v > 0 else '-' for v in row) if not np.isnan(row).any() else None
            for row in f[[a + suf for a in ACTORS]].values]
        f[f'str_{kind}'] = [
            ''.join('강' if abs(z) >= 1 else '약' for z in row) if not np.isnan(row).any() else None
            for row in f[[a + suf + '_z' for a in ACTORS]].values]
    fr, org = agg['frgn'], agg['orgn']
    f['absorb'] = (org / fr.abs()).where(fr < 0)
    c_fr, c_org = fr.rolling(20).sum(), org.rolling(20).sum()
    f['absorb20'] = (c_org / c_fr.abs()).where(c_fr < 0)
    return f


def pattern_name(p: str) -> str:
    return PATTERN_NAMES.get(p, 'N/A') if p else 'N/A'


# ── 사전 선언 v1 공통 계산 (tools/phase5_flows.py와 브리프가 같은 함수 사용) ──
def ecos_series(stat: str, item: str, start: str = '20130101') -> pd.Series:
    import requests
    key = os.environ['ECOS_KEY']
    js = requests.get(f'https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/9000/'
                      f'{stat}/D/{start}/{pd.Timestamp.now():%Y%m%d}/{item}', timeout=120).json()
    return pd.Series({pd.Timestamp(r['TIME']): float(r['DATA_VALUE'])
                      for r in js['StatisticSearch']['row']}).sort_index()


def external_frame(idx: pd.DatetimeIndex, kospi_close: pd.Series) -> pd.DataFrame:
    """2-4 설명변수 — 원/달러·원/엔 당일 변화%, 전야 SOX%, 전야 미10Y 변화(bp), 전일 KOSPI%."""
    from datetime import timedelta
    import yfinance as yf
    usd = ecos_series('731Y003', '0000003').pct_change() * 100
    jpy = ecos_series('731Y001', '0000002').pct_change() * 100

    def us_prev(sym, diff):
        h = yf.Ticker(sym).history(period='max', interval='1d')['Close']
        h.index = pd.DatetimeIndex(h.index).tz_localize(None).normalize()
        h = h[~h.index.duplicated()]
        return (h.diff() * 100) if diff else (h.pct_change() * 100)
    sox, tnx = us_prev('^SOX', False), us_prev('^TNX', True)
    kret = kospi_close.pct_change() * 100
    return pd.DataFrame({'usd': usd.reindex(idx), 'jpy': jpy.reindex(idx),
                         'sox': [sox.asof(d - timedelta(days=1)) for d in idx],
                         'tnx': [tnx.asof(d - timedelta(days=1)) for d in idx],
                         'kospi_prev': kret.shift(1).reindex(idx)}, index=idx)


def resid_expl(y: pd.Series, X: pd.DataFrame, win: int = 250) -> tuple:
    """OLS 창 t−win..t−1 적합 → t 예측. (잔차, 설명분)."""
    fit_hat = pd.Series(np.nan, index=y.index)
    Xv, yv = X.values, y.values
    for i in range(win, len(y)):
        Xw, yw = Xv[i - win:i], yv[i - win:i]
        ok = ~(np.isnan(Xw).any(axis=1) | np.isnan(yw))
        if ok.sum() < 150 or np.isnan(Xv[i]).any():
            continue
        A = np.column_stack([np.ones(ok.sum()), Xw[ok]])
        b, *_ = np.linalg.lstsq(A, yw[ok], rcond=None)
        fit_hat.iloc[i] = b[0] + Xv[i] @ b[1:]
    return y - fit_hat, fit_hat


def cluster_labels(f: pd.DataFrame, first_fit: str = '2016-06-30', only_last: bool = False) -> pd.Series:
    """2-3 walk-forward KMeans(k=4) — 월말까지로 적합, 다음 달 배정. C1..C4 = 중심 외인 좌표 오름차순."""
    from sklearn.cluster import KMeans
    Z = f[['frgn_c20_z', 'orgn_c20_z']]
    lab = pd.Series(np.nan, index=f.index)
    per = pd.DatetimeIndex(Z.index).to_period('M')
    months = list(per.unique())
    if only_last:                      # 브리프 — 마지막 달 배정에 필요한 직전 월 모델 하나만
        months = [months[-1] - 1]
    # 마지막 달(현재)도 직전 월말 모델로 배정되도록 루프는 직전 달까지
    for mth in months:
        end = mth.to_timestamp(how='end')
        if end < pd.Timestamp(first_fit):
            continue
        hist = Z[Z.index <= end].dropna()
        if len(hist) < 400:
            continue
        sel = (per == mth + 1) & Z.notna().all(axis=1).values
        if not sel.any():
            continue
        mu, sd = hist.mean(), hist.std()
        km = KMeans(n_clusters=4, n_init=20, random_state=0).fit(((hist - mu) / sd).values)
        order = np.argsort(km.cluster_centers_[:, 0])
        remap = {old: new + 1 for new, old in enumerate(order)}
        lab[sel] = [remap[q] for q in km.predict(((Z[sel] - mu) / sd).values)]
    return lab


def exhaustion_signals(f: pd.DataFrame) -> np.ndarray:
    """2-6 — 외인 cum20 z60 ≤ −2 후 20거래일 안 3일 연속 순매수 → 신호일 (첫 발생만, 20일 불응기)."""
    z, net = f['frgn_c20_z'].values, f['frgn_r'].values
    sig = np.zeros(len(f), bool)
    i, n = 0, len(f)
    while i < n:
        found = None
        if not np.isnan(z[i]) and z[i] <= -2:
            run = 0
            for j in range(i + 1, min(i + 21, n)):
                run = run + 1 if net[j] > 0 else 0
                if run == 3:
                    found = j
                    break
        if found is None:
            i += 1
            continue
        sig[found] = True
        i = found + 20
    return sig


def flows_state(p, kospi_close: pd.Series, with_model: bool = True) -> dict:
    """브리프·전방 로그용 — P일 3자 수급 상태 (market·block). 사실 정보만."""
    out = {}
    aggs = load_aggregates()
    ms = aggs['market_sum']
    out['market_sum_frgn_eok'] = (float(ms.loc[pd.Timestamp(p), 'frgn']) / 100
                                  if pd.Timestamp(p) in ms.index else None)
    mk = pd.read_parquet(MARKET_K51)
    out['k51_row'] = ({c: float(mk.loc[pd.Timestamp(p), c]) for c in ('frgn', 'natfor', 'ind')}
                      if pd.Timestamp(p) in mk.index else None)
    for univ in ('market', 'block', '000660'):
        a = aggs[univ]
        a = a[a.index <= pd.Timestamp(p)]
        f = features(a)
        last = f.index[-1]
        r = f.iloc[-1]
        st = {'date': last.date(), 'pat_cum20': r['pat_cum20'], 'pat_name': pattern_name(r['pat_cum20']),
              'str_cum20': r['str_cum20'], 'pat_daily': r['pat_daily'],
              'pat_daily_name': pattern_name(r['pat_daily']), 'str_daily': r['str_daily'],
              'absorb': None if np.isnan(r['absorb']) else float(r['absorb']),
              'absorb20': None if np.isnan(r['absorb20']) else float(r['absorb20']),
              'frgn20_eok': float(r['frgn_c20_amt']) / 100,
              'etc20_eok': float(r['etc_corp_c20_amt']) / 100,
              # 흡수율 v2 (2026-10-10, 별도 버전 — v1 검증 결과를 빌려 쓰지 않음): 기타법인 몫·기관+기타법인
              'absorb20_etc': (float(r['etc_corp_c20_amt'] / abs(r['frgn_c20_amt']))
                               if r['frgn_c20_amt'] < 0 else None),
              'absorb20_v2': (float((r['orgn_c20_amt'] + r['etc_corp_c20_amt']) / abs(r['frgn_c20_amt']))
                              if r['frgn_c20_amt'] < 0 else None),
              'exhaustion_signal': bool(exhaustion_signals(f)[-1])}
        if with_model and univ != '000660':
            try:
                st['cluster'] = cluster_labels(f, only_last=True).iloc[-1]
                st['cluster'] = None if np.isnan(st['cluster']) else int(st['cluster'])
                tail = f.iloc[-400:]
                X = external_frame(tail.index, kospi_close)
                resid, expl = resid_expl(tail['frgn_r'] * 100, X)
                st['frgn_resid'] = float(resid.iloc[-1]) if not np.isnan(resid.iloc[-1]) else None
                st['frgn_expl'] = float(expl.iloc[-1]) if not np.isnan(expl.iloc[-1]) else None
                st['frgn_resid_z'] = float(zprior(resid).iloc[-1])
                st['frgn_expl_z'] = float(zprior(expl).iloc[-1])
            except Exception as e:
                st['model_error'] = str(e)[:80]
        out[univ] = st
    return out
