# -*- coding: utf-8 -*-
"""큰돈 분할매수 방식 비교 — 전액즉시 vs 6개월 vs 12개월 vs 하락가속 (2026-09-13).

질문: "어느정도 규모의 금액을 몇 번에 나눠 매수하고 싶다 — 어떤 방법이 좋은가"
방법: 2015~2025의 각 시작일마다 동일 금액을 4가지 방식으로 투입하고, 시작일 +1년 후
      평가액을 비교 (시장 타이밍 예측 없음, 기계적 일정만).
  (a) 전액 즉시       — 시작일에 전부 매수
  (b) 6개월 균등      — 월 1회, 6개월에 걸쳐 균등
  (c) 12개월 균등     — 월 1회, 12개월에 걸쳐 균등
  (d) 하락가속 분할   — 주 1회 기본(26주 분할) + 고점대비 -20% 이하면 그 주 4배 투입
                        (사전에 약속된 기계 규칙 — 판단 개입 없음, -20% 이후 기대수익이
                         역사적으로 좋다는 사실을 일정 속도에만 반영)
지표: +1년 수익률의 평균/중앙값/최악, 전액즉시 대비 승률, 적립기간 중 포트폴리오 MDD.
"""
import asyncio
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dca_intensity_backtest import fetch_daily

HIGH_DD_ACCEL = -20.0   # 이 이하면 투입 속도 4배 (a priori)
ACCEL_X = 4


async def main():
    print('[1/3] 데이터 수집...')
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))
    from modules.semi_trigger.token_provider import get_semi_token
    closes = await fetch_daily(await get_semi_token())
    dd250 = (closes / closes.rolling(250).max() - 1) * 100

    print('[2/3] 시작일별 시뮬레이션...')
    starts = pd.date_range('2015-01-01', '2025-08-01', freq='20D')
    starts = [d for d in starts if d in closes.index]

    def deploy(start_ts, mode):
        """BUDGET=1.0 투입. 시작일부터 최대 12개월. (t, 남은예산, 유닛) 시뮬레이션 후 +1년 평가."""
        i0 = closes.index.get_loc(start_ts)
        budget, units = 1.0, 0.0
        path = []
        if mode == 'immediate':
            units = budget / closes.iloc[i0]
            budget = 0.0
            days = [i0]
        else:
            days = list(range(i0, min(i0 + 260, len(closes))))
        for i in days:
            p = closes.iloc[i]
            if mode == 'immediate':
                pass
            elif mode in ('m6', 'm12'):
                n = 6 if mode == 'm6' else 12
                # 매월 첫 거래일에 1/n씩 (월 시작 판단: 직전 행의 월과 다르면)
                if i == i0 or closes.index[i].month != closes.index[i - 1].month:
                    q = min(budget, 1.0 / n)
                    units += q / p
                    budget -= q
            elif mode == 'accel':
                # 주 1회(5거래일) 1/26, 고점대비 ≤ -20% 주엔 4/26
                if (i - i0) % 5 == 0 and budget > 0:
                    pace = (ACCEL_X / 26) if dd250.iloc[i] <= HIGH_DD_ACCEL else (1 / 26)
                    q = min(budget, pace)
                    units += q / p
                    budget -= q
            path.append((closes.index[i], units * p + budget))
        # +1년 시점 평가
        eval_ts = start_ts + pd.Timedelta(days=365)
        valid = closes.index[(closes.index >= eval_ts)]
        if len(valid) == 0:
            return None
        final_p = closes.asof(valid[0])
        final_v = units * final_p + budget
        # 적립기간 중 포트폴리오 MDD
        pv = pd.Series([v for _, v in path])
        mdd = float((pv / pv.cummax() - 1).min()) if len(pv) > 5 else 0.0
        return final_v - 1.0, mdd

    results = {'immediate': [], 'm6': [], 'm12': [], 'accel': []}
    mdds = {'immediate': [], 'm6': [], 'm12': [], 'accel': []}
    used = 0
    for s in starts:
        out = {}
        ok = True
        for mode in results:
            r = deploy(s, mode)
            if r is None:
                ok = False
                break
            out[mode] = r
        if not ok:
            continue
        used += 1
        for mode in results:
            results[mode].append(out[mode][0])
            mdds[mode].append(out[mode][1])

    print(f'[3/3] 결과 — 시작일 {used}개 (2015~2025), 각 +1년 평가\n')
    rows = []
    names = {'immediate': '(a) 전액 즉시', 'm6': '(b) 6개월 균등',
             'm12': '(c) 12개월 균등', 'accel': '(d) 하락가속 분할'}
    imm = np.array(results['immediate'])
    for mode in ('immediate', 'm6', 'm12', 'accel'):
        arr = np.array(results[mode])
        rows.append({
            '방식': names[mode],
            '평균': f"{arr.mean()*100:+.2f}%",
            '중앙값': f"{np.median(arr)*100:+.2f}%",
            '최악': f"{arr.min()*100:+.1f}%",
            '최선': f"{arr.max()*100:+.1f}%",
            '즉시 대비 승률': f"{(arr > imm).mean()*100:.0f}%" if mode != 'immediate' else '-',
            '적립중MDD': f"{np.mean(mdds[mode])*100:.1f}%",
        })
    print(pd.DataFrame(rows).to_string(index=False))

    print('\n[참고] 시작 시점 고점대비별 +1년 수익 (전액 즉시) — 하락 후 시작의 유리함:')
    for lo, hi in ((0, -10), (-10, -20), (-20, -30), (-30, -100)):
        sel = [r for r, s in zip(results['immediate'], starts)
               if lo >= dd250.asof(s) > hi]
        if sel:
            print(f"  고점대비 {lo}%~{hi}% 구간 시작 (n={len(sel)}): 평균 {np.mean(sel)*100:+.1f}%")


if __name__ == '__main__':
    asyncio.run(main())
