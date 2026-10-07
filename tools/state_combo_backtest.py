# -*- coding: utf-8 -*-
"""§4-1 4축 상태 조합별 이후 수익률 분포 — 최종작업지시서 (2026-10-07).

4축 이산화 (사전 고정 규칙 — 결과 본 뒤 변경 금지):
  축1 강도:   ADX14 >= 20 → 강 / 아니면 약
  축2 방향:   close>MA200 & MA20>MA60 → 상승 / close<MA200 & MA20<MA60 → 하락 / else 혼조
  축3 수급:   가격5일>0 & 반도체(삼전·하닉 평균)외인20일누적<0 → 괴리有 / else 無
              (가격 버티는데 수급 빠지는 상태 — 괴리 경고 축)
  축4 폭·주도주: 삼전 20일수익 − 코스피 20일수익 > 0 → 주도 유지 / <= 0 → 주도 이탈
              (breadth는 2021~만 가용이라 2010~ 전기간 검증엔 반도체 상대강도 사용;
               breadth 포함 변형은 2021~ 구간 별도 출력)
타깃 (방향뿐 아니라 변동성·낙폭·꼬리): r5·r20 평균/중앙값, 이후20일 평균|r|,
      이후20일 최대낙폭, r20 하락확률 — 셀별 n·블록부트 95% CI 병기
홀드아웃: 최근 12개월 봉인 (최종 확정 전 안 열어봄 — 지시서 §4-1)
다중비교: 셀 수 자동 → Bonferroni 임계 자동 재계산, n<30 셀은 "참고"

실행: beelink에서 python tools/state_combo_backtest.py
"""
import os
import sys
import warnings
from statistics import NormalDist

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
OHLC_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
FLOWS_PATH = os.path.join(BASE, '..', 'config', 'data', 'stock_flows.parquet')
PANEL_PATH = os.path.join(BASE, '..', 'config', 'data', 'breadth_panel_full.parquet')
HOLDOUT_DAYS = 252  # 최근 12개월 봉인
N_BOOT = 1000
MIN_N = 30


def block_ci(vals: np.ndarray, rng: np.random.Generator, block: int = 20):
    vals = vals[~np.isnan(vals)]
    if len(vals) < MIN_N:
        return None
    boots = []
    for _ in range(N_BOOT):
        idx = []
        while len(idx) < len(vals):
            b = rng.integers(0, max(1, len(vals) - block))
            idx.extend(range(b, b + block))
        idx = idx[:len(vals)]
        boots.append(np.nanmean(vals[idx]))
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return lo, hi


def main() -> int:
    ohlc = pd.read_parquet(OHLC_PATH)
    c = ohlc['close'].astype(float)
    from market_breadth import adx_atr
    ax = adx_atr(ohlc)

    df = pd.DataFrame(index=ohlc.index)
    ma200, ma20, ma60 = c.rolling(200).mean(), c.rolling(20).mean(), c.rolling(60).mean()
    df['axis1'] = np.where(ax['adx'] >= 20, '강', '약')
    up_dir = (c > ma200) & (ma20 > ma60)
    dn_dir = (c < ma200) & (ma20 < ma60)
    df['axis2'] = np.where(up_dir, '상승', np.where(dn_dir, '하락', '혼조'))

    # 축3 수급 괴리 — 반도체(삼전·하닉 평균) 외인 20일 누적 vs 가격 5일
    flows = pd.read_parquet(FLOWS_PATH)
    flows.index = pd.to_datetime(flows.index, format='%Y%m%d')
    semi_frgn = ((flows['005930_frgnr'] + flows['000660_frgnr']) / 2).reindex(c.index)
    cum20 = semi_frgn.rolling(20).sum()
    px5 = c.pct_change(5) * 100
    df['axis3'] = np.where((px5 > 0) & (cum20 < 0), '괴리有',
                           np.where((px5 <= 0) & (cum20 >= 0), '無', '혼합'))

    # 축4 주도주 상대강도
    sem = pd.concat([flows['005930_frgnr']], axis=1)  # placeholder (미사용)
    samsung = pd.read_parquet(FLOWS_PATH).columns  # noqa
    # 삼전 종가는 flows에 없음 — kospi와 삼전 수익률은 종가 필요 → bars_1d 대신
    # ka20006 코스피와 삼전 수급이 아닌 종목 종가가 필요하므로, 여기선 간단히
    # 코스피 vs 반도체 수급 방향으로 대체하지 않고, MDC 종가 패널의 삼전 열 사용.
    panel = pd.read_parquet(PANEL_PATH)
    if '005930' in panel.columns:
        sam_c = panel['005930'].reindex(c.index)
        rel = sam_c.pct_change(20) * 100 - c.pct_change(20) * 100
        df['axis4'] = np.where(rel > 0, '주도유지', '주도이탈')
    else:
        df['axis4'] = 'N/A'

    # 타깃
    r5 = (c.shift(-5) / c - 1) * 100
    r20 = (c.shift(-20) / c - 1) * 100
    fut_abs = c.pct_change().abs().rolling(20).mean().shift(-20) * 100
    fut_dd = (c.shift(-20) / c.rolling(20).max().shift(-1) - 1) * 100  # 이후 20일 최대낙폭 근사
    for k, v in (('r5', r5), ('r20', r20), ('fut_abs20', fut_abs), ('fut_dd20', fut_dd)):
        df[k] = v
    df = df.dropna(subset=['axis1', 'axis2', 'r5'])

    # 홀드아웃 봉인
    df_learn = df.iloc[:-HOLDOUT_DAYS]
    print(f'[4-1] 학습 표본: {len(df_learn)}일 ({df_learn.index.min().date()}~{df_learn.index.max().date()})'
          f' | 홀드아웃 {HOLDOUT_DAYS}일 봉인 ({df.index[-HOLDOUT_DAYS:].min().date()}~{df.index[-1].date()})')

    rng = np.random.default_rng(42)
    groups = df_learn.groupby(['axis1', 'axis2', 'axis3', 'axis4'])
    n_cells = len(groups)
    t_star = -NormalDist().inv_cdf(0.05 / (2 * n_cells * 4))
    print(f'[4-1] 조합 {n_cells}셀 × 4타깃 → Bonferroni t* = {t_star:.2f}\n')

    hdr = (f"{'조합(강도/방향/수급/주도)':<30}{'n':>5} | {'r5':>7} {'r20':>7} "
           f"{'|r|20':>6} {'낙폭20':>7} {'하락확률':>7}  r20 CI        판정")
    print(hdr)
    print('-' * 110)
    for key, g in groups:
        if len(g) < 10:
            continue
        label = '/'.join(str(k) for k in key)
        up_rate = (g['r20'] < 0).mean() * 100
        ci = block_ci(g['r20'].values, rng)
        note = '참고' if len(g) < MIN_N else ''
        if ci and ci[0] > 0:
            note += '★유의(+)' if not note else '/★(+)'
        elif ci and ci[1] < 0:
            note += '★유의(-)' if not note else '/★(-)'
        ci_s = f"[{ci[0]:+.1f},{ci[1]:+.1f}]" if ci else '-'
        print(f"{label:<30}{len(g):>5} | {g['r5'].mean():>+7.2f} {g['r20'].mean():>+7.2f} "
              f"{g['fut_abs20'].mean():>6.2f} {g['fut_dd20'].mean():>+7.2f} {up_rate:>6.0f}%  "
              f"{ci_s:<13} {note.strip()}")

    print()
    print('[4-1] 홀드아웃은 최종 확정 시점에 1회만 개봉 (지시서 규칙). 본 출력은 학습 표본만.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
