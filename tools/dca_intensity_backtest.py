# -*- coding: utf-8 -*-
"""DCA 적립강도 조절 백테스트 — 단일축(200일 이평 이격도) vs Plain (2026-09-08).

Lee 설계 (2026-09-08 승인안):
  - 수요일 주간 DCA (= DCAbot 실제 주기) 2015~2026, 069500 (KODEX 200)
  - 배수 함수: 이격도 ≥+10% → 1.3 / ≥0% → 1.0 / ≥-10% → 0.8 / 그 외 → 0.6
  - 미투자분은 계좌 내 현금으로 보유 (수익 0) — 세 버전 공정 비교
  - 실행가능성: 배수는 매수일 직전 거래일 종가 기준 (당일 종가 신호로 당일 종가 매수는
    불가능하므로 1거래일 lag — look-ahead 제거)
  - 지표: IRR(금액가중 연율) / 배율(최종자산-투입원금) / MDD / Sharpe / Sortino
  - Walk-forward: 2015-19 / 2020-22 / 2023-26 구간별 안정성 (파라미터는 a priori라 재최적화 없음)
  - 민감도: 임계값·배수 격자 9조합 — 보고 전용 (골라잡기 금지)

데이터: 키움 ka10081 페이징 (600행/페이지, next-key) — 국내 단일 소스.
"""
import asyncio
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'automation'))

CODE = '069500'
START = '20140101'          # 200일 이평 워밍업용
END = '20260908'
WEEKLY_BUDGET = 100_000     # 수요일마다 예산 (동일 예산이면 배율 비교 공정)
BUY_FEE = 0.0002            # 0.02% — 매수 수수료 (근사)
MA_DAYS = 200

# 배수 함수 파라미터 (a priori — 최적화 금지, 민감도는 격자 보고만)
TH_UP = 10.0
TH_DN = -10.0
M_UP, M_MID, M_LOW, M_BEAR = 1.3, 1.0, 0.8, 0.6


def intensity_multiplier(disparity_pct: float) -> float:
    if disparity_pct >= TH_UP:
        return M_UP
    if disparity_pct >= 0:
        return M_MID
    if disparity_pct >= TH_DN:
        return M_LOW
    return M_BEAR


# ── 데이터: 키움 ka10081 페이징 ──────────────────────────────
async def fetch_daily(token: str) -> pd.Series:
    from utils.rate_limiter import requests
    import utils.config as config

    rows, cont, nk = [], 'N', ''
    base_dt = END
    for _page in range(20):
        r = await requests.post(
            config.get_host_url() + '/api/dostk/chart',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {token}',
                     'cont-yn': cont, 'next-key': nk, 'api-id': 'ka10081'},
            json={'stk_cd': CODE, 'base_dt': base_dt, 'upd_stkpc_tp': '1'})
        d = r.json()
        if d.get('return_code') != 0:
            raise RuntimeError(f"ka10081 실패: {d.get('return_msg')}")
        rows.extend(d.get('stk_dt_pole_chart_qry') or [])
        cont = r.headers.get('cont-yn', 'N')
        nk = r.headers.get('next-key', '')
        if cont != 'Y':
            break
    out = {}
    for it in rows:
        dt = str(it.get('dt', ''))
        close = abs(float(it.get('cur_prc') or 0))
        if dt and close > 0:
            out[datetime.strptime(dt, '%Y%m%d')] = close
    s = pd.Series(out).sort_index()
    s.index = pd.to_datetime(s.index)
    return s


# ── 백테스트 엔진 ───────────────────────────────────────────
def run_dca(closes: pd.Series, multiplier_fn=None, start='2015-01-01',
            end='2026-08-31', weekly_budget=WEEKLY_BUDGET):
    """수요일 DCA. multiplier_fn(prev_disparity) → 배수 (None=Plain).

    배수는 매수일 직전 거래일의 이격도로 결정 (실행가능 신호).
    반환: result dict + weekly DataFrame.
    """
    ma = closes.rolling(MA_DAYS).mean()
    disparity = (closes / ma - 1) * 100

    buys = closes.loc[start:end]
    # 수요일 종료 주차 샘플 (수요일 휴장이면 그 주 마지막 거래일)
    wed = buys.groupby(pd.Grouper(freq='W-WED')).last().dropna()

    units, cash, invested = 0.0, 0.0, 0.0
    recs = []
    for buy_dt, price in wed.items():
        mult = 1.0
        if multiplier_fn is not None:
            prev_disp = disparity.loc[:buy_dt - pd.Timedelta(days=1)]
            if prev_disp.dropna().empty:
                mult = 1.0
            else:
                mult = multiplier_fn(float(prev_disp.iloc[-1]))
        budget = weekly_budget * mult
        fee = budget * BUY_FEE
        u = (budget - fee) / price
        units += u
        cash += weekly_budget - budget        # 미투자분은 현금 보유
        invested += weekly_budget
        wealth = units * price + cash
        recs.append({'date': buy_dt, 'price': price, 'mult': mult,
                     'units': units, 'cash': cash, 'invested': invested,
                     'wealth': wealth})

    df = pd.DataFrame(recs).set_index('date')
    final_price = float(closes.iloc[-1])
    final_wealth = units * final_price + cash
    years = (df.index[-1] - df.index[0]).days / 365.25

    # IRR (금액가중 연율) — 주차별 유출(-budget), 최종 유입(+final_wealth)
    dates = list(df.index) + [closes.index[-1]]
    flows = [-weekly_budget] * len(df) + [final_wealth]
    yrs = [(d - dates[0]).days / 365.25 for d in dates]
    def npv(rate):
        return sum(f / (1 + rate) ** y for f, y in zip(flows, yrs))
    lo, hi = -0.9, 3.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    irr = (lo + hi) / 2

    # 주간 수익률 (신규 납입 제외한 포트폴리오 수익)
    prev_w = df['wealth'].shift(1)
    contrib = weekly_budget
    r = (df['wealth'] - contrib) / prev_w - 1
    r = r.dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(52)) if r.std() > 0 else np.nan
    downside = r[r < 0]
    sortino = float(r.mean() / downside.std() * np.sqrt(52)) if len(downside) > 1 and downside.std() > 0 else np.nan

    # MDD (동일 납입 경로라 버전 간 상대비교 유효)
    roll_max = df['wealth'].cummax()
    mdd = float(((df['wealth'] / roll_max) - 1).min())

    return {
        'irr': irr, 'mult_final': final_wealth / invested, 'mdd': mdd,
        'sharpe': sharpe, 'sortino': sortino, 'n_buys': len(df),
        'invested': invested, 'final_wealth': final_wealth,
    }, df


def summarize(name, res):
    return {'버전': name,
            'IRR(연율)': f"{res['irr']*100:+.2f}%",
            '배율': f"{res['mult_final']:.3f}",
            'MDD': f"{res['mdd']*100:.1f}%",
            'Sharpe': f"{res['sharpe']:.2f}",
            'Sortino': f"{res['sortino']:.2f}",
            '매수횟수': res['n_buys']}


async def main():
    print('[1/4] 키움 ka10081 페이징 수집 (069500, 2014~)...')
    from modules.semi_trigger.token_provider import get_semi_token
    token = await get_semi_token()
    closes = await fetch_daily(token)
    print(f"  {len(closes)}행 {closes.index[0].date()} ~ {closes.index[-1].date()}")

    print('[2/4] 백테스트: Plain vs 단일축...')
    rows = []
    res_plain, df_plain = run_dca(closes, None)
    res_single, df_single = run_dca(closes, intensity_multiplier)
    rows.append(summarize('Plain DCA (배수 1.0)', res_plain))
    rows.append(summarize('단일축 (0.6~1.3배)', res_single))

    print('[3/4] 구간별 안정성 (walk-forward 윈도우)...')
    windows = [('2015-2019', '2015-01-01', '2019-12-31'),
               ('2020-2022', '2020-01-01', '2022-12-31'),
               ('2023-2026', '2023-01-01', '2026-08-31')]
    for wname, ws, we in windows:
        p, _ = run_dca(closes, None, ws, we)
        s, _ = run_dca(closes, intensity_multiplier, ws, we)
        rows.append(summarize(f'  [{wname}] Plain', p))
        rows.append(summarize(f'  [{wname}] 단일축', s))

    print('[4/4] 민감도 격자 (보고 전용 — 골라잡기 금지)...\n')
    print('=== 결과 요약 ===')
    print(pd.DataFrame(rows).to_string(index=False))

    print('\n=== 민감도: 임계값 × 배수범위 (전기간 IRR / 배율 / MDD) ===')
    grid_rows = []
    for th in (5.0, 10.0, 15.0):
        for (lo_m, hi_m) in ((0.6, 1.3), (0.7, 1.2), (0.8, 1.1)):
            def fn(d, th=th, lo_m=lo_m, hi_m=hi_m):
                if d >= th: return hi_m
                if d >= 0: return 1.0
                if d >= -th: return lo_m
                return 0.6 if lo_m <= 0.6 else lo_m - 0.2
            res, _ = run_dca(closes, fn)
            grid_rows.append({'임계': f'±{th:.0f}%', '배수': f'{lo_m}~{hi_m}',
                              'IRR': f"{res['irr']*100:+.2f}%",
                              '배율': f"{res['mult_final']:.3f}",
                              'MDD': f"{res['mdd']*100:.1f}%"})
    print(pd.DataFrame(grid_rows).to_string(index=False))

    # 참고: 매수 분포 — 배수가 실제로 쓰였는지
    print('\n=== 배수 사용 분포 (단일축, 전기간) ===')
    print(df_single['mult'].value_counts().sort_index().to_string())

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dca_intensity_weekly.csv')
    df_single.to_csv(out, encoding='utf-8-sig')
    print(f'\n주간 기록 저장: {out}')


if __name__ == '__main__':
    asyncio.run(main())
