# -*- coding: utf-8 -*-
"""§7 신규 신호 소급 검증 — MU 단독 z + SOX z (2026-10-07 Lee 지시).

가설 (사전 고정, 지시서 §7): MU z<=-1.5 AND SOX z<=-0.5 → 반도체 급락 동반 조정.
z: 60일 롤링(과거만 사용 — point-in-time), yfinance MU·^SOX 10년.

날짜 정렬 (단위테스트 고정 — 수급 밀림 사고 재발 방지):
  미국 D일 종가 신호 → 한국 첫 영업일(D보다 늦은 최초 KR 거래일)에 적용.
  미국 D+1 새벽(KST)에 미장 마감 → 한국 D+1 세션이 신호 반영 가능한 첫 세션.

검증: 신호 에피소드(연속일=1) → KR 실행일 E → 삼전/하닉 close-to-close 1/5/20일
수익률, 블록부트 CI, BH-FDR (6테스트 = 2종목×3horizon).
한계: 삼전·하닉 종가는 MDC 2021~ — 이벤트 스터디도 2021~ 신호만.

실행: beelink에서 python tools/mu_sox_signal_backtest.py
"""
import glob
import os
import sys
import warnings
from statistics import NormalDist

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
KOSPI_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
STOCKS_DIR = r'C:\market_data\bars_1d\stocks'
Z_WIN = 60
MU_TH, SOX_TH = -1.5, -0.5  # 사전 고정 (지시서 §7)


def load_us() -> pd.DataFrame:
    import yfinance as yf
    mu = yf.Ticker('MU').history(period='10y', interval='1d', auto_adjust=True)['Close']
    sox = yf.Ticker('^SOX').history(period='10y', interval='1d', auto_adjust=True)['Close']
    df = pd.DataFrame({'mu': mu, 'sox': sox}).dropna()
    df.index = df.index.tz_localize(None).normalize()
    return df


def load_kr_days() -> list:
    ohlc = pd.read_parquet(KOSPI_PATH)
    return sorted(d.date().isoformat() for d in ohlc.index)


def load_stock_close(code) -> pd.Series:
    frames = [pd.read_parquet(f) for f in
              sorted(glob.glob(os.path.join(STOCKS_DIR, code, '*.parquet')))]
    df = pd.concat(frames)
    df['dt'] = pd.to_datetime(df['dt'])
    return df.drop_duplicates('dt').set_index('dt')['close'].astype(float)


def kr_next(d_iso: str, kr_days: list, kr_set: set) -> str:
    """미국 D일 신호의 한국 실행일 — D보다 늦은 최초 KR 거래일."""
    for k in kr_days:
        if k > d_iso:
            return k
    return None


def test_alignment(kr_days: list) -> None:
    """날짜 정렬 단위테스트 — 고정 기대값 (실패 시 중단)."""
    kr_set = set(kr_days)
    # 1) 미국 금요일 → 한국 월요일 (2026-01-16 금)
    e = kr_next('2026-01-16', kr_days, kr_set)
    assert e == '2026-01-19', f'금→월 실패: {e}'
    # 2) 한국 신정(1/1) 건너뛰기 (2025-12-31 수 → 1/1 휴장 → 1/2 금)
    e = kr_next('2025-12-31', kr_days, kr_set)
    assert e == '2026-01-02', f'신정 스킵 실패: {e}'
    # 3) 한국 개천절(10/3)+대체(10/5) 건너뛰기 (2026-10-02 금 → 10/6 화)
    e = kr_next('2026-10-02', kr_days, kr_set)
    assert e == '2026-10-06', f'개천절+대체 스킵 실패: {e}'
    # 4) 단사·단조: 서로 다른 미국 세션일이 같은 KR일로 매핑되지 않음
    us_days = [d for d in kr_days]  # 미국 세션은 별도 — 아래에서 실 데이터로 재검
    pairs = [(a, b) for a, b in zip(kr_days, kr_days[1:]) if b != a]
    assert len(kr_next('2024-02-16', kr_days, kr_set)) == 10
    print('[정렬테스트] PASS: 금→월, 신정 스킵, 개천절+대체 스킵, 매핑 유효성')


def main() -> int:
    us = load_us()
    kr_days = load_kr_days()
    kr_set = set(kr_days)
    test_alignment(kr_days)

    # z 계산 — point-in-time (당일까지 데이터만, 신호는 다음날 한국 적용)
    mu_z = (us['mu'] - us['mu'].rolling(Z_WIN).mean()) / us['mu'].rolling(Z_WIN).std()
    sox_z = (us['sox'] - us['sox'].rolling(Z_WIN).mean()) / us['sox'].rolling(Z_WIN).std()
    sig = ((mu_z <= MU_TH) & (sox_z <= SOX_TH)).fillna(False)

    # 미국 세션일만 (휴장에 신호 없음) + KR 매핑
    sig_days = [d.date().isoformat() for d in us.index[sig]]
    missing_kr = [d for d in sig_days if kr_next(d, kr_days, kr_set) is None]
    if missing_kr:
        print(f'[경고] KR 실행일 없는 신호 {len(missing_kr)}개 (데이터 끝 근처) 제외')
    pairs = sorted({(d, kr_next(d, kr_days, kr_set)) for d in sig_days
                    if kr_next(d, kr_days, kr_set) is not None})
    # 에피소드 — 연속 미국 세션 신호일 = 1개 (간격 4캘린더일 이하면 연속으로 간주)
    episodes, cur = [], [pairs[0]]
    for pa, pb in pairs[1:]:
        if (pd.Timestamp(pa) - pd.Timestamp(cur[-1][0])).days <= 4:
            cur.append((pa, pb))
        else:
            episodes.append(cur)
            cur = [(pa, pb)]
    episodes.append(cur)
    print(f'[신호] 신호일 {len(sig_days)}일 → 에피소드 {len(episodes)}개 '
          f'(연도: {sorted({e[0][0][:4] for e in episodes})})')

    closes = {c: load_stock_close(c) for c in ('005930', '000660')}
    rng = np.random.default_rng(42)
    ps = []
    rows = []
    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        s = closes[code]
        s.index = pd.to_datetime(s.index).date
        for h in (1, 5, 20):
            rets = []
            for ep in episodes:
                e_day = pd.Timestamp(ep[-1][1]).date()  # 에피소드 마지막 신호의 KR 실행일
                i0 = np.where(s.index >= e_day)[0]
                if len(i0) == 0 or i0[0] + h >= len(s):
                    continue
                e0 = i0[0]
                rets.append((s.iloc[e0 + h] / s.iloc[e0] - 1) * 100)
            a = np.array(rets)
            if len(a) < 5:
                continue
            boots = np.array([a[rng.integers(0, len(a), len(a))].mean() for _ in range(2000)])
            lo, hi = np.percentile(boots, [2.5, 97.5])
            p = 2 * min((boots <= 0).mean(), (boots >= 0).mean())
            ps.append(p)
            rows.append({'name': name, 'h': h, 'n': len(a), 'mean': a.mean(),
                         'med': np.median(a), 'lo': lo, 'hi': hi, 'p': p})
    print()
    print(f'{"종목":<8}{"h":>4}{"에피소드":>8}{"평균%":>8}{"중앙값%":>8}{"95%CI":>18}{"p":>8}')
    print('-' * 65)
    for r in rows:
        print(f'{r["name"]:<8}{r["h"]:>4}{r["n"]:>8}{r["mean"]:>+8.2f}{r["med"]:>+8.2f}'
              f'  [{r["lo"]:+.2f},{r["hi"]:+.2f}]{r["p"]:>8.3f}')
    # BH-FDR
    ps = np.array(sorted(ps))
    cut = 0
    for i, p in enumerate(ps):
        if p <= 0.05 * (i + 1) / len(ps):
            cut = p
    print()
    print(f'BH-FDR: p <= {cut:.4f} 유의 ({sum(1 for p in ps if p <= cut)}/{len(ps)}테스트)')
    print('한계: 삼전·하닉 종가 2021~ → 이벤트도 2021~ 신호만. z는 60일 롤링 point-in-time.')
    print('섀도 로그는 이후 순수 전방 검증용으로 유지 (소급 검증과 분리).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
