# -*- coding: utf-8 -*-
"""블록 v2 이력 재계산 — semis_detail 기반 (2026-10-07 Lee 지시).

v2 정의: 반도체 블록 본값 = (삼성전자 + SK하이닉스) / 2 — 소부장 균등평균 왜곡 제거.
이력 연속 유지를 위해 과거 전 행을 semis_detail(이미 저장된 삼전·하닉 값)로 재계산.
rotation_spread = v2 블록 − others_ret. 삭제 없이 값 갱신만 (백업 후 적용).
실행: beelink에서 python tools/recalc_block_v2.py
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))


def main() -> int:
    from macro_monitor import JSONL_PATH, load_history, upsert_daily_record

    hist = load_history()
    backup = JSONL_PATH + '.bak_blockv2_20261007'
    shutil.copy2(JSONL_PATH, backup)
    n_fixed = n_skip = 0
    for r in hist:
        d = r.get('semis_detail') or {}
        sam, hyn = d.get('삼성전자'), d.get('SK하이닉스')
        others = r.get('others_ret')
        if sam is None or hyn is None or others is None:
            n_skip += 1
            continue
        block_v2 = round((sam + hyn) / 2, 2)
        rec = {'date': r['date'], 'semis_ret': block_v2,
               'rotation_spread': round(block_v2 - others, 2)}
        upsert_daily_record(rec)
        n_fixed += 1
    print(f'[block_v2] 재계산 {n_fixed}행 / 스킵(detail 부족) {n_skip}행 / 백업 {backup}')
    print('리포트 상관·기준 대비는 다음 16:50 실행에서 재계산된 history로 갱신됨.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
