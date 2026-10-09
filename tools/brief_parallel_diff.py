# -*- coding: utf-8 -*-
"""병행 운영 diff — 통합 브리프 vs 기존 알림(거시 모니터·semi) 공통 수치 자동 비교.

비교 대상 (정의 동일): 국면 라벨, 고점 대비, 3개월, ATR14%, ATR %ile, 삼전·하닉 MA60 괴리
비교 제외 (정의 변경 — 차이가 정상): 폭(정의 v2: KRX 확정·주권 유니버스), 외인 z(종목 합산
  시계열), 전야 가중 US(MU·SNDK 가중 vs 4종 평균), 미10Y·원달러(거시 모니터 16:50 vs 브리프
  다음날 아침 — 반영 미국 세션이 다름)
허용: 기존 알림의 표시 반올림 자릿수만큼 (dd·mom 0.05, ATR 0.005, %ile 0.5, MA60 0.05)
  python tools/brief_parallel_diff.py   → 일자별 표 + 'diff 0' 판정
"""
import json
import os
import re
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
sys.path.insert(0, os.path.join(ROOT, 'automation'))
B = os.path.join(ROOT, 'config', 'data', 'brief')
TOL = {'dd': 0.05, 'mom': 0.05, 'atr_pct': 0.005, 'atr_pctile': 0.5, 'ma60': 0.05}
EXCLUDED = ['폭(정의 v2)', '외인 z(종목 합산 시계열)', '전야 가중 US(정의 변경)',
            '미10Y·원달러(반영 세션 상이)']


def main() -> int:
    from macro_monitor import load_history
    from modules.semi_trigger.kr_calendar import prev_kr_trading_day
    briefs = [json.loads(l) for l in open(os.path.join(B, 'briefs.jsonl'), encoding='utf-8')]
    legacy = []
    lp = os.path.join(B, 'legacy_log.jsonl')
    if os.path.exists(lp):
        legacy = [json.loads(l) for l in open(lp, encoding='utf-8')]
    macro = {r['date']: r for r in load_history()}
    rows = []
    for b in briefs:
        if not b.get('final'):
            continue                      # 확정 브리프만 비교
        p = pd.Timestamp(prev_kr_trading_day(pd.Timestamp(b['exec']).date())).strftime('%Y%m%d')
        m, bm = macro.get(p, {}), b['metrics']
        reg, br = m.get('regime') or {}, m.get('breadth') or {}
        cmp = [('국면', bm['regime'], reg.get('label'), None),
               ('고점대비', bm['dd'], reg.get('dd'), TOL['dd']),
               ('3개월', bm['mom'], reg.get('mom'), TOL['mom']),
               ('ATR14%', bm['atr_pct'], br.get('atr14_pct'), TOL['atr_pct']),
               ('ATR %ile', bm['atr_pctile'], br.get('atr14_pctile'), TOL['atr_pctile'])]
        semi = [x for x in legacy if x['kind'] == 'semi' and x['date'].replace('-', '') == p]
        for code, nm in (('005930', '삼성전자'), ('000660', 'SK하이닉스')):
            leg = None
            if semi:
                mm = re.search(rf'\[{code}\].*?종가 ([\d,]+) / MA60 ([\d,]+)', semi[-1]['text'], re.S)
                if mm:
                    c, ma = (float(v.replace(',', '')) for v in mm.groups())
                    leg = (c / ma - 1) * 100
            cmp.append((f'{nm} MA60', bm.get(f'{code}_ma60'), leg, TOL['ma60']))
        for item, a, l, tol in cmp:
            if l is None or a is None:
                ok = None
            elif tol is None:
                ok = a == l
            else:
                ok = abs(a - l) <= tol
            rows.append({'exec': b['exec'], 'P': p, 'item': item, 'brief': a, 'legacy': l, 'ok': ok})
    df = pd.DataFrame(rows)
    if df.empty:
        print('비교할 확정 브리프 없음')
        return 0
    print(df.to_string(index=False))
    checked = df.dropna(subset=['ok'])
    bad = checked[~checked['ok'].astype(bool)]
    days = sorted(set(df['exec']))
    print(f'\n확정 브리프 {len(days)}일 | 비교 {len(checked)}건 | 불일치 {len(bad)}건 | '
          f'기존값 없음 {int(df["ok"].isna().sum())}건 → {"diff 0" if bad.empty else "diff 있음"}')
    print('비교 제외(정의 변경):', ', '.join(EXCLUDED))
    return 0


if __name__ == '__main__':
    sys.exit(main())
