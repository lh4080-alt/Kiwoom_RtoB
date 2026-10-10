# -*- coding: utf-8 -*-
"""semi 섀도·4축 전방 로그 무효 플래그 (2026-10-10 Lee 지시 — 삭제·값 수정 없음, 백업 후 플래그만).

semi_shadow.jsonl: 정규 실행(RtoB_Semi_Snapshot0530, logged_at 05:2x~05:4x)이 아닌 행 → invalid._row
  (2026-10-06 13:57·10-07 11:00 수동 테스트 행 — date 형식 YYYYMMDD, 10-10 14:33 date 2026-06-01 행)
forward_log.jsonl: 섀도 테스트 행에서 읽힌 signal 필드 → invalid.signal_mu_sox / signal_v2_sox_only
"""
import json
import os
import shutil
import sys
from datetime import datetime

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'data')


def is_regular(r) -> bool:
    t = r.get('logged_at', '')[11:16]
    return '05:20' <= t <= '05:45'


def rewrite(path, fn) -> int:
    rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    shutil.copy2(path, path + f'.bak_flag_{datetime.now():%Y%m%d_%H%M%S}')
    n = sum(fn(r) for r in rows)
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(path + '.tmp', path)
    return n


def main() -> int:
    shadow = os.path.join(D, 'semi_shadow.jsonl')
    bad = []

    def f_shadow(r):
        if is_regular(r) or '_row' in (r.get('invalid') or {}):
            return 0
        r['invalid'] = {**(r.get('invalid') or {}),
                        '_row': f"정규 05:30 실행 아님 (logged_at {r.get('logged_at')}) — 수동·테스트 기록"}
        bad.append((r['date'], r['code']))
        return 1
    n1 = rewrite(shadow, f_shadow)
    bad_dates = {str(d).replace('-', '') for d, _ in bad}

    def f_fwd(r):
        if r.get('date') in bad_dates and r.get('signal_mu_sox') is not None:
            why = f"semi 섀도 비정규 행(date {r['date']})에서 읽은 값 — 기록 시점 조인 폐지(2026-10-10)"
            r['invalid'] = {**(r.get('invalid') or {}), 'signal_mu_sox': why, 'signal_v2_sox_only': why}
            return 1
        return 0
    n2 = rewrite(os.path.join(D, 'forward_log.jsonl'), f_fwd)
    # macro jsonl axes_signal — 같은 값의 복사본
    sys.path.insert(0, os.path.join(D, '..', '..', 'automation'))
    from macro_monitor import JSONL_PATH, load_history, upsert_daily_record
    shutil.copy2(JSONL_PATH, JSONL_PATH + f'.bak_flag_{datetime.now():%Y%m%d_%H%M%S}')
    n3 = 0
    for r in load_history(raw=True):
        if r['date'] in bad_dates and r.get('axes_signal') is not None:
            upsert_daily_record({'date': r['date'], 'invalid': {
                'axes_signal': '섀도 비정규 행에서 복사된 값 — 조인 폐지(2026-10-10)'}})
            n3 += 1
    print(f'섀도 무효 행 {n1}: {bad} | 전방 로그 신호 무효 {n2}행 | macro axes_signal 무효 {n3}행')
    return 0


if __name__ == '__main__':
    sys.exit(main())
