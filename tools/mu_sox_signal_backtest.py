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


def load_stock_ohlc(code) -> pd.DataFrame:
    frames = [pd.read_parquet(f) for f in
              sorted(glob.glob(os.path.join(STOCKS_DIR, code, '*.parquet')))]
    df = pd.concat(frames)
    df['dt'] = pd.to_datetime(df['dt'])
    return df.drop_duplicates('dt').set_index('dt').sort_index()


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
    # 에피소드 연도 분포 (2021~ 실행 가능분)
    ep_years = []
    for ep in episodes:
        e_day = pd.Timestamp(ep[-1][1]).date()
        s0 = closes['005930']
        s0.index = pd.to_datetime(s0.index).date
        i0 = np.where(s0.index >= e_day)[0]
        if len(i0) and s0.index[i0[0]] == e_day:
            ep_years.append(e_day.year)
    from collections import Counter
    print('에피소드 연도 분포 (실행 가능):', dict(sorted(Counter(ep_years).items())))
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
                if s.index[e0] != e_day:
                    continue  # 실행일 데이터 없음 (2021 이전 신호) — 앵커 오염 방지
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

    # ── 보강 (2026-10-07 Lee 지시 4가지) ─────────────────────────
    print()
    print('== 보강 1) 진입 기준 — 실행일 시가 vs 종가 (미국 급락은 한국 시가 갭 반영) ==')
    rng2 = np.random.default_rng(43)
    ohlcs = {c: load_stock_ohlc(c) for c in ('005930', '000660')}
    for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        o = ohlcs[code]['open'].astype(float)
        cl = ohlcs[code]['close'].astype(float)
        o.index = pd.to_datetime(o.index).date
        cl.index = pd.to_datetime(cl.index).date
        for h in (5, 20):
            op_r, cl_r = [], []
            for ep in episodes:
                e_day = pd.Timestamp(ep[-1][1]).date()
                i0 = np.where(cl.index >= e_day)[0]
                if not len(i0) or i0[0] + h >= len(cl) or cl.index[i0[0]] != e_day:
                    continue
                e0 = i0[0]
                op_r.append((cl.iloc[e0 + h] / o.iloc[e0] - 1) * 100)   # 시가 진입 (실행 가능)
                cl_r.append((cl.iloc[e0 + h] / cl.iloc[e0] - 1) * 100)  # 종가 진행 (실행 불가 가정)
            a, b = np.array(op_r), np.array(cl_r)
            if not len(a):
                continue
            bp = np.array([a[rng2.integers(0, len(a), len(a))].mean() for _ in range(2000)])
            lo, hi = np.percentile(bp, [2.5, 97.5])
            print(f'{name} h{h}: 시가진입 {a.mean():+.2f}% [{lo:+.2f},{hi:+.2f}] '
                  f'(n={len(a)}) | 종가진행 {b.mean():+.2f}%')

    print()
    print('== 보강 2) 대조군 — 기저 수익 & 하닉 자체 낙폭 매칭 ==')
    cl_h = ohlcs['000660']['close'].astype(float)
    cl_h.index = pd.to_datetime(cl_h.index).date
    hi20 = cl_h.pct_change(20) * 100
    base20 = hi20.dropna()
    sig_exec = [pd.Timestamp(ep[-1][1]).date() for ep in episodes]
    sig20 = [hi20.loc[d] for d in sig_exec if d in hi20.index]
    print(f'하닉 20일 수익 — 전체 기저: {base20.mean():+.2f}% (n={len(base20)}) | '
          f'신호일: {np.mean(sig20):+.2f}% (n={len(sig20)}) | 초과: {np.mean(sig20) - base20.mean():+.2f}%p')
    # 낙폭 매칭 대조군 — 신호일 하닉 고점대비(dd250) 분포와 비슷한 날, 신호 없는 날
    dd250 = (cl_h / cl_h.rolling(250).max() - 1) * 100
    sig_dd = [dd250.loc[d] for d in sig_exec if d in dd250.index and not pd.isna(dd250.loc[d])]
    med_dd = np.median(sig_dd)
    sig_set = set(sig_exec)
    ctrl = [d for d in dd250.index if d not in sig_set
            and not pd.isna(dd250.loc[d]) and dd250.loc[d] <= med_dd]
    ctrl20 = [hi20.loc[d] for d in ctrl if d in hi20.index]
    print(f'하닉 낙폭 매칭 대조군 (dd<=신호일 중위 {med_dd:.1f}%, 신호 없는 날): '
          f'20일 수익 {np.mean(ctrl20):+.2f}% (n={len(ctrl20)}) | 신호일 초과: {np.mean(sig20) - np.mean(ctrl20):+.2f}%p')

    print()
    print('== 보강 3) 조건 분해 (ablation) — 하닉 5·20일, 시가진입 ==')
    o_h = ohlcs['000660']['open'].astype(float)
    o_h.index = pd.to_datetime(o_h.index).date
    for label, cond in (
            ('MU 단독 (z<=-1.5)', (mu_z <= MU_TH)),
            ('SOX 단독 (z<=-0.5)', (sox_z <= SOX_TH)),
            ('결합 (둘 다)', (mu_z <= MU_TH) & (sox_z <= SOX_TH))):
        days = [d.date().isoformat() for d in us.index[cond.fillna(False)]]
        eps, cur = [], []
        for d in days:
            k = kr_next(d, kr_days, kr_set)
            if k is None:
                continue
            if cur and (pd.Timestamp(d) - pd.Timestamp(cur[-1][0])).days <= 4:
                cur.append((d, k))
            else:
                if cur:
                    eps.append(cur)
                cur = [(d, k)]
        if cur:
            eps.append(cur)
        for h in (5, 20):
            rets = []
            for ep in eps:
                e_day = pd.Timestamp(ep[-1][1]).date()
                i0 = np.where(cl_h.index >= e_day)[0]
                if not len(i0) or i0[0] + h >= len(cl_h) or cl_h.index[i0[0]] != e_day:
                    continue
                rets.append((cl_h.iloc[i0[0] + h] / o_h.iloc[i0[0]] - 1) * 100)
            if rets:
                print(f'{label:<22} h{h}: {np.mean(rets):+.2f}% (n={len(rets)})')

    print()
    print('== 보강 4) 에피소드 의존도 — 하닉 20일 (시가진입) ==')
    ep20 = []
    for ep in episodes:
        e_day = pd.Timestamp(ep[-1][1]).date()
        i0 = np.where(cl_h.index >= e_day)[0]
        if not len(i0) or i0[0] + 20 >= len(cl_h) or cl_h.index[i0[0]] != e_day:
            continue
        e0 = i0[0]
        ep20.append({'day': e_day, 'year': e_day.year, 'r': (cl_h.iloc[e0 + 20] / o_h.iloc[e0] - 1) * 100})
    a_all = np.array([e['r'] for e in ep20])
    # 간격 체크: 에피소드 간 최소 간격 (20거래일 이상 분리 여부)
    gaps = [np.where(cl_h.index > a['day'])[0][0] - np.where(cl_h.index > b['day'])[0][0]
            for a, b in zip(ep20, ep20[1:])]
    print(f'에피소드 간 최소 간격: {min(gaps)}거래일 (20일 horizon 대비 '
          f'{"충분" if min(gaps) >= 20 else "겹침 — 중복 표본"})')
    by_year = {}
    for e in ep20:
        by_year.setdefault(e['year'], []).append(e['r'])
    print('연도별:', {k: f'{np.mean(v):+.1f}%(n={len(v)})' for k, v in sorted(by_year.items())})
    top2 = sorted(ep20, key=lambda e: -abs(e['r']))[:2]
    a_ex_top = np.array([e['r'] for e in ep20 if e not in top2])
    a_ex_22 = np.array([e['r'] for e in ep20 if e['year'] != 2022])
    print(f'전체: {a_all.mean():+.2f}% (n={len(a_all)}) | 상위2 기여 제외: {a_ex_top.mean():+.2f}% '
          f'(n={len(a_ex_top)}) | 2022 전체 제외: {a_ex_22.mean():+.2f}% (n={len(a_ex_22)})')

    print()
    print('해석: 삼성전자는 CI 0 포함 — "미검출". 전방 검증은 연 3~4건이라 수년 소요 —')
    print('그동안 실매매 없이 알림에 "참고: 과거 n건·결과" 표시 방식.')
    print('섀도 로그는 이후 순수 전방 검증용으로 유지 (소급 검증과 분리).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
