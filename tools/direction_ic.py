# -*- coding: utf-8 -*-
"""방향 확률 층 검증 — KOSPI 향후 20일 수익률에 대한 방향 후보 IC (2026-10-09 Lee 지시).

사전 고정 규칙 (결과 확인 전 기록):
  타깃     y_t = KOSPI close_{t+20} / close_t − 1 (거래일), 평가 표본 2014-07-01 ~ 마지막−20일
  통계량   Spearman IC(x_t, y_t)
  null     원형 이동 — x를 y에 대해 k칸(60 ≤ k ≤ N−60, 무작위 2,000회) 이동. 자기상관·겹침
           구조를 보존하고 정렬만 깨뜨림 → p(양측) = (#|IC_k| ≥ |IC| + 1)/(K+1)
  다중검정 BH-FDR q ≤ 0.10 (전체 표본, 검정한 후보 전체)
  안정성   2014-07~2019-12 / 2020-01~ 두 구간 IC 부호 일치 (구간별 원형 이동 p도 보고)
  통과     FDR 통과 AND 부호 일치 — 통과 변수만 합성 점수 후보
정렬 (look-ahead 금지 — 모두 t일 KR 장 마감 시점에 알려진 값):
  국면     compute_regime(t까지 KOSPI) → 강세장 +1 / 보합·전환기 0 / 하락장 −1
  10개월MA 직전 완료 월말 종가 > 그 시점 10개월 월말 종가 평균 → 1, 아니면 0 (다음 월말까지 유지)
  모멘텀   3·6·12개월(63·126·252거래일) KOSPI 수익률
  외인 z   종목 합산 시장 외인(기타외국인 제외, 주권) z20·z60 — 브리프와 동일 정의 (장기 이력 있을 때)
  미10Y    t보다 이른 마지막 미국 세션의 ^TNX − 20세션 전 (bp)
  원달러   ECOS 731Y003/0000003(서울 15:30 종가) t일 / 20거래일 전 − 1 (%)
  폭       장기 패널(ka10081) A/D 20일 비율·200일선 위 비율 (있을 때)
  반도체수출 미확보 (관세청 품목별 API는 공공데이터포털 키 필요)
출력: config/data/verify/direction_ic.csv, 콘솔 표
"""
import os
import sys
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
D = os.path.join(ROOT, 'config', 'data')
H = 20
EVAL_START = pd.Timestamp('2014-07-01').date()
SPLIT = pd.Timestamp('2020-01-01').date()
K_SHIFTS = 2000
MIN_SHIFT = 60
rng = np.random.default_rng(20261009)


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 100:
        return np.nan
    return float(pd.Series(x[m]).rank().corr(pd.Series(y[m]).rank()))


def shift_test(x: np.ndarray, y: np.ndarray, k: int = K_SHIFTS) -> tuple:
    ic = spearman(x, y)
    n = len(x)
    if np.isnan(ic) or n < 2 * MIN_SHIFT + 10:
        return ic, np.nan
    ks = rng.integers(MIN_SHIFT, n - MIN_SHIFT, size=k)
    null = np.array([spearman(np.roll(x, s), y) for s in ks])
    return ic, float((np.sum(np.abs(null) >= abs(ic)) + 1) / (k + 1))


def load_candidates(k: pd.Series) -> pd.DataFrame:
    from macro_monitor import compute_regime
    idx = list(k.index)
    c = k.values
    out = pd.DataFrame(index=idx)
    # 국면 (point-in-time)
    reg = []
    lab = {'강세장': 1, '보합·전환기': 0, '하락장': -1}
    for i in range(len(idx)):
        if i < 260:
            reg.append(np.nan)
            continue
        r = compute_regime({d.isoformat(): v for d, v in zip(idx[max(0, i - 599):i + 1],
                                                              c[max(0, i - 599):i + 1])})
        reg.append(lab.get(r['label']) if r else np.nan)
    out['국면'] = reg
    # 10개월 이평 규칙 — 월말 종가 기준, 다음 월말까지 유지
    s = pd.Series(c, index=pd.to_datetime(idx))
    me = s.groupby([s.index.year, s.index.month]).tail(1)
    sig = (me > me.rolling(10).mean()).astype(float).where(me.rolling(10).mean().notna())
    sig.index = [d.date() for d in sig.index]
    out['10개월MA'] = pd.Series(sig).reindex(idx).ffill().values
    for m, n in ((3, 63), (6, 126), (12, 252)):
        out[f'모멘텀{m}개월'] = (s / s.shift(n) - 1).values * 100
    # 외인 z (장기 이력 있을 때 — 브리프와 같은 정의: 당일 값 vs 직전 N일)
    # 시장 단위 원천 ka10051(억원) — 종목 합산 이력은 생존편향으로 교체 (Phase 5 v1.1, 2026-10-10)
    p = os.path.join(D, 'market_flows_k51.parquet')
    if os.path.exists(p):
        f = pd.read_parquet(p)['frgn']
        f.index = [d.date() for d in pd.DatetimeIndex(f.index)]
        f = f.reindex(idx)
        for w in (20, 60):
            mu = f.rolling(w).mean().shift(1)
            sd = f.rolling(w).std().shift(1)
            out[f'외인z{w}'] = ((f - mu) / sd).values
    # 미10Y 20세션 변화 — t보다 이른 마지막 미국 세션
    import yfinance as yf
    tnx = yf.Ticker('^TNX').history(period='max', interval='1d')['Close']
    tnx.index = [d.date() for d in pd.DatetimeIndex(tnx.index).tz_localize(None)]
    tnx = tnx[~pd.Index(tnx.index).duplicated()].sort_index()
    chg = (tnx - tnx.shift(20)) * 100
    cs = pd.Series(chg.values, index=pd.to_datetime(chg.index))
    out['미10Y_20일변화'] = [cs.asof(pd.Timestamp(d - timedelta(days=1))) for d in idx]
    # 원달러 20거래일 변화 — ECOS 15:30 종가
    key = os.environ.get('ECOS_KEY')
    if key:
        import requests
        js = requests.get(f'https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/9000/'
                          f'731Y003/D/20120101/{idx[-1]:%Y%m%d}/0000003', timeout=120).json()
        fx = pd.Series({pd.Timestamp(r['TIME']).date(): float(r['DATA_VALUE'])
                        for r in js['StatisticSearch']['row']}).sort_index()
        fx = fx.reindex(idx)
        out['원달러_20일변화'] = (fx / fx.shift(20) - 1).values * 100
    # 폭 — 장기 패널 (있을 때)
    p = os.path.join(D, 'breadth_k81_panel.parquet')
    if os.path.exists(p):
        pnl = pd.read_parquet(p)
        pnl.index = [d.date() for d in pd.DatetimeIndex(pnl.index)]
        pnl = pnl.reindex(idx)
        chg_ = pnl.pct_change(fill_method=None)
        up, dn = (chg_ > 0).sum(axis=1), (chg_ < 0).sum(axis=1)
        out['AD20일비율'] = (up.rolling(20).sum() / dn.rolling(20).sum().replace(0, np.nan)).values
        ma = pnl.rolling(200, min_periods=200).mean()
        above = (pnl > ma).sum(axis=1)
        valid = (pnl.notna() & ma.notna()).sum(axis=1)
        out['200일선위비율'] = (above / valid.replace(0, np.nan) * 100).values
    return out


def main() -> int:
    k = pd.read_parquet(os.path.join(D, 'kospi_daily_ohlc.parquet'))['close'].astype(float)
    k.index = [d.date() for d in pd.DatetimeIndex(k.index)]
    y_all = (k.shift(-H) / k - 1) * 100
    cand = load_candidates(k)
    ev = [d for d in k.index if d >= EVAL_START and not np.isnan(y_all[d])]
    y = y_all.reindex(ev).values
    print(f'평가 표본 {len(ev)}일 ({ev[0]}~{ev[-1]}) | 기저 상승확률 {np.mean(y > 0) * 100:.1f}% '
          f'| 평균 20일 수익 {np.mean(y):+.2f}%\n')
    rows = []
    for name in cand.columns:
        x = cand[name].reindex(ev).values.astype(float)
        cover = int((~np.isnan(x)).sum())
        ic, p = shift_test(x, y)
        sub = {}
        for lbl, m in (('전반', np.array([d < SPLIT for d in ev])),
                       ('후반', np.array([d >= SPLIT for d in ev]))):
            sub[lbl] = shift_test(x[m], y[m], k=500)
        rows.append({'후보': name, '관측': cover, 'IC': ic, 'p': p,
                     'IC_전반': sub['전반'][0], 'p_전반': sub['전반'][1],
                     'IC_후반': sub['후반'][0], 'p_후반': sub['후반'][1]})
    df = pd.DataFrame(rows)
    valid = df['p'].notna()
    ps = df.loc[valid, 'p'].sort_values()
    m = len(ps)
    cut = 0.0
    for i, (ix, pv) in enumerate(ps.items(), 1):
        if pv <= 0.10 * i / m:
            cut = pv
    df['FDR10'] = df['p'].le(cut) & valid & (cut > 0)
    df['부호일치'] = np.sign(df['IC_전반']) == np.sign(df['IC_후반'])
    df['통과'] = df['FDR10'] & df['부호일치']
    print(f'{"후보":<14}{"관측":>6}{"IC":>8}{"p":>8}{"전반IC":>8}{"후반IC":>8}  FDR10 부호 판정')
    print('-' * 78)
    for _, r in df.iterrows():
        print(f"{r['후보']:<14}{r['관측']:>6}{r['IC']:>+8.3f}{r['p']:>8.4f}{r['IC_전반']:>+8.3f}"
              f"{r['IC_후반']:>+8.3f}  {'✓' if r['FDR10'] else '·':^5} {'✓' if r['부호일치'] else '✗':^4}"
              f" {'통과' if r['통과'] else '미통과'}")
    print(f'\nBH-FDR 10% 임계 p ≤ {cut:.4f} (검정 {m}건) | 반도체 수출: 미확보 (공공데이터포털 키 필요)')
    df.to_csv(os.path.join(D, 'verify', 'direction_ic.csv'), index=False, encoding='utf-8-sig')
    return 0


if __name__ == '__main__':
    sys.exit(main())
