# -*- coding: utf-8 -*-
"""V5 검증 리포트 — V1~V4 결과를 1개 markdown + CSV로 (일일 브리프 지시서).

입력: config/data/verify/v1_summary.json, v1_market_foreign.json, v2_summary.json,
      tests (pytest 실행), tests/fixtures/brief_*.json (V4 재실행)
출력: config/data/verify/V5_report.md, V5_report.csv
판정 열: 통과 / 실패 / 미확보 / 브리프 처리 (실패 원인이 브리프 경로에서 해소된 항목 — 근거 병기)
"""
import csv
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, '..')
V = os.path.join(ROOT, 'config', 'data', 'verify')
sys.path.insert(0, os.path.join(ROOT, 'automation'))

# V1 실패 중 브리프 경로에서 해소된 항목 — 해소 근거 (사전 기록)
RESOLVED = {
    'A/D 최종일 (패널vsKRX확정)': '브리프는 최종일을 ka10066 KRX 확정값으로 대체 (breadth_frame) — V3 재생 대조 통과',
    '패널 종가 최종일 (표본30)': '동일 — MDC 최종일(15:35 잠정)은 브리프에서 사용하지 않음',
    '미10Y (참고 FRED)': '참고 대조 — 정의 차이(재무부 고시). 1차 대조는 네이버 로이터',
    '원달러 (참고 FRED)': '참고 대조 — 정의 차이(뉴욕 정오). 브리프는 ⚠️ 미검증 표기',
}
FLAGGED = {
    '원달러': '정의 일치 2차 출처 미확보 → 브리프에 "⚠️ 미검증" 표기 (ECOS 키 발급 시 해소)',
    '미10Y': '59/60일 일치 — 9/10 1일 0.017%p (CBOE 15:00 vs 로이터 17:05 마감시각) → Lee 판단 대기',
}


def load(name):
    p = os.path.join(V, name)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def main() -> int:
    rows = []
    for s in load('v1_summary.json') or []:
        if s['item'] == '시장 합계 외인 순매수':
            continue      # 아래 종목 합산 독립 검증으로 대체
        if s['pass'] is None:
            verdict = '미확보'
        elif s['pass']:
            verdict = '통과'
        elif s['item'] in RESOLVED:
            verdict = '브리프 처리'
        else:
            verdict = '실패'
        note = RESOLVED.get(s['item']) or FLAGGED.get(s['item']) or s.get('note', '')
        rows.append({'단계': 'V1 외부 대조', '항목': s['item'], 'n': s['n'], '실패': s['n_fail'],
                     '최대오차': s['max_err'], '허용': s['tol'], '판정': verdict,
                     '원인': json.dumps(s.get('causes') or {}, ensure_ascii=False), '비고': note})
    mf = load('v1_market_foreign.json')
    if mf:
        ok = mf['diff_eok'] == 0 and mf['stock_match'].split('/')[0] == mf['stock_match'].split('/')[1]
        rows.append({'단계': 'V1 외부 대조', '항목': '시장 합계 외인 (종목 합산 대조)',
                     'n': mf['stock_match'], '실패': 0 if ok else 1, '최대오차': mf['diff_eok'],
                     '허용': 0, '판정': '통과' if ok else '실패', '원인': '{}',
                     '비고': f"Σka10059(KOSPI 주권) {mf['sum_ka10059_eok']:,}억 = ka10066 "
                             f"{mf['sum_ka10066_eok']:,}억 ({mf['last_session']}), 이력 {mf['days']}일"})
    for s in load('v2_summary.json') or []:
        legacy = '기존 도구' in s['item']
        rows.append({'단계': 'V2 독립 재계산', '항목': s['item'], 'n': s['n'], '실패': s['n_fail'],
                     '최대오차': s['max_err'], '허용': s['tol'],
                     '판정': ('통과' if s['pass'] else ('브리프 처리' if legacy else '실패')),
                     '원인': '{}', '비고': ('기존 v3~v5 도구의 ddof 편향 — 브리프는 brief.calc 사용 (통과)'
                                        if legacy else s.get('note', ''))})
    # V3 pytest
    r = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_brief_golden.py',
                        'tests/test_kr_calendar.py', 'tests/test_upsert_merge.py', '-q'],
                       cwd=ROOT, capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [''])[-1]
    rows.append({'단계': 'V3 골든 테스트', '항목': 'pytest (골든·세션·서머타임·캘린더·upsert)',
                 'n': last, '실패': 0 if r.returncode == 0 else 1, '최대오차': '', '허용': '',
                 '판정': '통과' if r.returncode == 0 else '실패', '원인': '{}',
                 '비고': '픽스처: ' + ', '.join(os.path.basename(p) for p in sorted(
                     glob.glob(os.path.join(ROOT, 'tests', 'fixtures', 'brief_*.json'))))})
    # V4 — 픽스처 + 최근 기록에 대한 무결성 결과
    from brief.integrity import run_checks
    from brief.serde import loads
    for p in sorted(glob.glob(os.path.join(ROOT, 'tests', 'fixtures', 'brief_*.json'))):
        fx = json.load(open(p, encoding='utf-8'))
        f = run_checks(loads(json.dumps(fx['d'])))
        rows.append({'단계': 'V4 무결성', '항목': f'무결성 검사 {os.path.basename(p)[6:14]}',
                     'n': 5, '실패': len(f), '최대오차': '', '허용': 0,
                     '판정': '통과' if not f else '실패', '원인': json.dumps(f, ensure_ascii=False),
                     '비고': '시점·범위·단위·동일값·look-ahead'})

    os.makedirs(V, exist_ok=True)
    cols = ['단계', '항목', 'n', '실패', '최대오차', '허용', '판정', '원인', '비고']
    with open(os.path.join(V, 'V5_report.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    cnt = {}
    for r_ in rows:
        cnt[r_['판정']] = cnt.get(r_['판정'], 0) + 1
    md = [f'# V5 검증 리포트 — 일일 브리프 ({datetime.now():%Y-%m-%d %H:%M})', '',
          '판정 집계: ' + ' · '.join(f'{k} {v}' for k, v in cnt.items()), '',
          '| 단계 | 항목 | n | 실패 | 최대오차 | 허용 | 판정 | 비고 |', '|---|---|---|---|---|---|---|---|']
    for r_ in rows:
        me = r_['최대오차']
        me = f'{me:.4g}' if isinstance(me, float) else me
        md.append(f"| {r_['단계']} | {r_['항목']} | {r_['n']} | {r_['실패']} | {me} | {r_['허용']} | "
                  f"**{r_['판정']}** | {r_['비고']} |")
    open(os.path.join(V, 'V5_report.md'), 'w', encoding='utf-8').write('\n'.join(md) + '\n')
    print('\n'.join(md))
    return 0


if __name__ == '__main__':
    sys.exit(main())
