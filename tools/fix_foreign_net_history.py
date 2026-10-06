# -*- coding: utf-8 -*-
"""foreign_net_eok 이력 정리 (2026-10-06).

배경: foreign_net_eok에 소스가 혼재돼 있었음
  - 당일: ka10066 16종목 ETF 합 (시장 대표성 부족)
  - 전일 보완: ka10058 종목 랭킹 상위 100 합 (시장 전체와 무관 — 판명)
둘 다 "코스피 시장 전체 외국인 순매수"가 아니므로 전 행 None 처리하고,
오늘부터 ka10066 코스피 전종목 합산으로 새로 축적.

flows(16종목 개별 수급)는 ka10066 기반으로 유효 — 유지.

실행: beelink에서 python tools/fix_foreign_net_history.py  (단발)
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'automation'))


def main() -> int:
    from macro_monitor import JSONL_PATH, load_history, upsert_daily_record

    hist = load_history()
    targets = [r for r in hist if r.get('foreign_net_eok') is not None]
    if not targets:
        print('[foreign_net_fix] 정리 대상 없음')
        return 0

    # 백업
    backup = JSONL_PATH + '.bak_foreign_net_20261006'
    shutil.copy2(JSONL_PATH, backup)
    print(f'[foreign_net_fix] 백업: {backup}')

    for r in targets:
        upsert_daily_record({'date': r['date'], 'foreign_net_eok': None})
    print(f'[foreign_net_fix] foreign_net_eok None 처리: {len(targets)}행 '
          f'({targets[0]["date"]}~{targets[-1]["date"]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
