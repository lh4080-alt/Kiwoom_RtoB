# -*- coding: utf-8 -*-
"""브리프 작업 4 백테스트 — 정기 적립 vs 삼전 회차 당김 / 국면별 비중 ON·OFF (2026-10-09).

사전 고정 규칙 (결과 확인 전 기록 — 변경 시 새 버전):
  정기    brief_config.DCA_DAY/DCA_FREQ (주 1회 목요일, 휴장이면 다음 거래일) 시가 매수, 회당 1
  당김    삼전만. 같은 주기 정기일 이전 KR 거래일 E에서 트리거(가중 z ≤ Z_TH 또는 가중 누적 ≤ RET_TH)
          최초 발생 시 E 시가로 매수하고 그 주기 정기 매수는 생략. 하닉은 정기만.
  트리거  E에 반영되는 미국 세션(직전 KR 거래일 P ~ E−1) 복리 누적 × 가중치(MU·SNDK, SNDK 상장 전은 MU 단독),
          z = (x − n·μ)/(σ·√n), μ·σ = 직전 SIGMA_WINDOW 세션 일간 가중수익 (브리프와 동일 함수 정의)
  비용    매수 1회 COST_BUY (양 전략 매수 횟수 동일 — 차이에 영향 없음, 절대값만 반영)
  검정    순열 10,000회 — 당김이 발생한 주기마다 '같은 주기 정기일 이전 거래일' 중 하나를 무작위
          선택해 평균단가 분포 생성 → 실제 당김 평균단가 이하 비율 = p (단측, 낮을수록 개선)
  국면    직전 KR 거래일 KOSPI로 compute_regime (point-in-time). 사전 선언 2안:
          역추세 {강세장 0.75, 보합·전환기 1.0, 하락장 1.25} / 추세추종 {1.25, 1.0, 0.75}
          지표: 평균단가, 최종 평가액/투입액, 최저 평가손익률(= min(평가액/누적투입 − 1))
데이터: KR pykrx 수정주가(V1 ka10081 대조 0원), 미국 yfinance(V1 Nasdaq 대조), KOSPI ka20006 parquet.
"""
import os
import sys
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd

warnings.simplefilter('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
import brief_config as cfg  # noqa: E402
from brief.dca import cycle_of, scheduled_exec  # noqa: E402

START = '20120101'
N_PERM = 10_000
REGIME_PLANS = {'역추세': {'강세장': 0.75, '보합·전환기': 1.0, '하락장': 1.25},
                '추세추종': {'강세장': 1.25, '보합·전환기': 1.0, '하락장': 0.75}}


def load():
    from pykrx import stock
    import yfinance as yf
    kr = {}
    for code in ('005930', '000660'):
        # pykrx(네이버 원천)는 1회 약 3,000행 제한 — 구간을 나눠 받아 병합
        parts = []
        for a, b in (('20120101', '20171231'), ('20180101', date.today().strftime('%Y%m%d'))):
            px = stock.get_market_ohlcv(a, b, code, adjusted=True)
            parts.append(px)
        px = pd.concat(parts)
        px = px[~px.index.duplicated()].sort_index()
        px.index = [d.date() for d in pd.DatetimeIndex(px.index)]
        px = px[['시가', '종가']].rename(columns={'시가': 'open', '종가': 'close'}).astype(float)
        # 시가 0 = 거래정지일 (예: 삼전 2018 액면분할) → 매수 불가일로 제외
        kr[code] = px[(px['open'] > 0) & (px['close'] > 0)]
    us = {}
    for s in ('MU', 'SNDK'):
        h = yf.Ticker(s).history(period='max', interval='1d', auto_adjust=True)['Close']
        h.index = [d.date() for d in pd.DatetimeIndex(h.index).tz_localize(None)]
        us[s] = h[~pd.Index(h.index).duplicated()].sort_index()
    k = pd.read_parquet(os.path.join(ROOT, 'config', 'data', 'kospi_daily_ohlc.parquet'))
    k.index = [d.date() for d in pd.DatetimeIndex(k.index)]
    return kr, us, k['close'].astype(float)


def trigger_series(kr_days: list, us: dict, weights: dict) -> pd.DataFrame:
    """KR 실행일별 가중 누적 x, z, 트리거 여부 — 브리프 정의와 동일."""
    rets = pd.DataFrame({s: v.pct_change() * 100 for s, v in us.items()})
    w = pd.Series(weights)
    avail = rets.notna().astype(float).mul(w, axis=1)
    daily = (rets.fillna(0).mul(w, axis=1)).sum(axis=1) / avail.sum(axis=1).replace(0, np.nan)
    us_days = sorted(daily.dropna().index)
    us_set = set(us_days)
    rows = []
    for i in range(1, len(kr_days)):
        e, p = kr_days[i], kr_days[i - 1]
        sess = [d for d in (p + timedelta(days=k) for k in range((e - p).days)) if d in us_set]
        if not sess:
            rows.append({'exec': e, 'x': np.nan, 'z': np.nan})
            continue
        x = float((np.prod([1 + daily[d] / 100 for d in sess]) - 1) * 100)
        prior = daily[[d < sess[0] for d in daily.index]].iloc[-cfg.SIGMA_WINDOW:]
        z = (float((x - len(sess) * prior.mean()) / (prior.std(ddof=1) * np.sqrt(len(sess))))
             if len(prior) == cfg.SIGMA_WINDOW else np.nan)
        rows.append({'exec': e, 'x': x, 'z': z})
    t = pd.DataFrame(rows).set_index('exec')
    t['trig'] = (t['z'] <= cfg.Z_TH) | (t['x'] <= cfg.RET_TH)
    return t


def schedule(kr_days: list) -> dict:
    """주기 → (정기일, 정기일 이전 같은 주기 거래일 목록)."""
    cyc = {}
    for d in kr_days:
        c = cycle_of(d)
        cyc.setdefault(c, []).append(d)
    out = {}
    for c, days in cyc.items():
        s = scheduled_exec(c)
        if s not in days:          # 정기일이 거래정지일 → 같은 주기 다음 매수 가능일
            later = [d for d in days if d > s]
            if not later:
                continue
            s = later[0]
        out[c] = (s, [d for d in days if d < s])
    return out


def avg_price(buys: list, opens: pd.Series, amounts: list = None) -> float:
    amounts = amounts or [1.0] * len(buys)
    units = sum(a * (1 - cfg.COST_BUY) / opens[d] for d, a in zip(buys, amounts))
    return sum(amounts) / units


def main() -> int:
    from macro_monitor import compute_regime
    kr, us, kospi = load()
    sam = kr['005930']
    days = list(sam.index)
    sch = schedule(days)
    t = trigger_series(days, us, cfg.STOCK_US_WEIGHTS['005930'])

    # ── A) 정기 vs 당김 (삼전) ──
    reg_buys, pull_buys, pulled_cycles = [], [], []
    for c, (s, before) in sorted(sch.items()):
        reg_buys.append(s)
        hit = next((d for d in before if d in t.index and bool(t.loc[d, 'trig'])), None)
        if hit:
            pull_buys.append(hit)
            pulled_cycles.append(c)
        else:
            pull_buys.append(s)
    op = sam['open']
    ap_reg, ap_pull = avg_price(reg_buys, op), avg_price(pull_buys, op)
    rng = np.random.default_rng(42)
    null = []
    pc_set = set(pulled_cycles)
    cyc_list = sorted(sch.items())
    for _ in range(N_PERM):
        buys = [(before[rng.integers(len(before))] if (c in pc_set and before) else s)
                for c, (s, before) in cyc_list]
        null.append(avg_price(buys, op))
    null = np.array(null)
    p = float((np.sum(null <= ap_pull) + 1) / (N_PERM + 1))
    print(f'== A) 삼전 정기 vs 회차 당김 ({days[0]}~{days[-1]}, 주기 {len(sch)}) ==')
    print(f'  당김 발생 주기 {len(pulled_cycles)} ({len(pulled_cycles) / len(sch) * 100:.1f}%)')
    print(f'  평균단가 정기 {ap_reg:,.0f}원 | 당김 {ap_pull:,.0f}원 | 차이 {(ap_pull / ap_reg - 1) * 100:+.2f}%')
    print(f'  순열(같은 주기 정기일 이전 무작위일) 평균단가 중앙 {np.median(null):,.0f}원 '
          f'[{np.percentile(null, 2.5):,.0f}~{np.percentile(null, 97.5):,.0f}] → p={p:.4f} (단측)')
    for label, lo, hi in (('2012~2019', date(2012, 1, 1), date(2019, 12, 31)),
                          ('2020~', date(2020, 1, 1), date(2099, 1, 1))):
        rb = [d for d in reg_buys if lo <= d <= hi]
        pb = [d for d in pull_buys if lo <= d <= hi]
        print(f'  [{label}] 정기 {avg_price(rb, op):,.0f} | 당김 {avg_price(pb, op):,.0f} | '
              f'차이 {(avg_price(pb, op) / avg_price(rb, op) - 1) * 100:+.2f}%')

    # ── B) 국면별 비중 ON/OFF (정기 일정, 양 종목) ──
    print('\n== B) 국면별 비중 조정 (정기 일정) ==')
    kd = list(kospi.index)
    reg_by_day = {}
    for s, _ in sch.values():
        prev = [d for d in kd if d < s]
        if len(prev) < 260:
            continue
        sub = kospi.loc[prev[-600:]]
        r = compute_regime({d.isoformat(): v for d, v in sub.items()})
        reg_by_day[s] = r['label'] if r else None
    for code, nm in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
        px = kr[code]
        buys = [s for s, _ in sorted(sch.values()) if s in reg_by_day and s in px.index]
        print(f'-- {nm} (매수 {len(buys)}회) --')
        for plan, mult in [('OFF', None)] + list(REGIME_PLANS.items()):
            amts = [1.0 if mult is None else mult.get(reg_by_day[s], 1.0) for s in buys]
            scale = len(buys) / sum(amts)            # 총투입 동일 정규화 (비교용)
            amts = [a * scale for a in amts]
            units, invested, worst = 0.0, 0.0, 0.0
            for s, a in zip(buys, amts):
                units += a * (1 - cfg.COST_BUY) / px.loc[s, 'open']
                invested += a
                worst = min(worst, units * px.loc[s, 'close'] / invested - 1)
            final = units * px['close'].iloc[-1] / invested
            print(f'  {plan:<6} 평균단가 {invested / units:,.0f} | 최종 배수 {final:.3f} | '
                  f'최저 평가손익률 {worst * 100:+.1f}%')
    print('\n결과 보고만 — 적용 여부는 Lee 결정 (DCA_REGIME_SCALING 기본 OFF 유지)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
