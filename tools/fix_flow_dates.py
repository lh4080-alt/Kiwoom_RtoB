# -*- coding: utf-8 -*-
"""jsonl flows·foreign_net_eok 날짜 재배치 (2026-10-07).

확정된 문제: ka10066 16:50 조회는 '전일 거래일 확정분'을 반환 → 운영이 당일 행에
기록해 하루 밀림 (검증: jsonl 9/17 = ka10059 9/16, 10/6 기록 = 10/2 분).

재배치: 행 d에 있는 값 = 실제 prev_trade(d)의 값 → prev_trade(d) 행으로 이동.
(거래일 리스트는 kospi_daily_ohlc.parquet 기준 — 휴장 자동 처리)
실행: beelink에서 python tools/fix_flow_dates.py  (백업 후 적용)
"""
import json
import os
import shutil
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, '..', 'automation'))
OHLC_PATH = os.path.join(BASE, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')


def main() -> int:
    from macro_monitor import JSONL_PATH, load_history, upsert_daily_record

    ohlc = pd.read_parquet(OHLC_PATH)
    tdays = [d.strftime('%Y%m%d') for d in ohlc.index]
    tset = set(tdays)
    prev_of = {}
    for i in range(1, len(tdays)):
        prev_of[tdays[i]] = tdays[i - 1]

    hist = load_history()
    # 수집: 행 d의 flows/foreign_net → prev_trade(d)로
    moved = {}  # new_date -> {'flows':..., 'foreign_net_eok':...}
    for r in hist:
        d = r['date']
        has_flow = r.get('flows') is not None
        has_fn = r.get('foreign_net_eok') is not None
        if not (has_flow or has_fn):
            continue
        if d not in prev_of:
            print(f'[fix] {d}: 이전 거래일 없음 — 이동 불가, 폐기')
            continue
        tgt = prev_of[d]
        if tgt not in tset:
            tgt = prev_of.get(tgt)  # 이중 안전
        rec = moved.setdefault(tgt, {})
        if has_flow:
            rec['flows'] = r['flows']
        if has_fn:
            rec['foreign_net_eok'] = r['foreign_net_eok']

    # 백업
    backup = JSONL_PATH + '.bak_flows_20261007'
    shutil.copy2(JSONL_PATH, backup)
    print(f'[fix] 백업: {backup}')

    # 적용: 대상 행들의 기존 flows/fn 제거 후 새 값 기록
    touched = {r['date'] for r in hist if r.get('flows') is not None
               or r.get('foreign_net_eok') is not None}
    for d in sorted(touched | set(moved)):
        rec = {'date': d}
        if d in moved:
            rec.update(moved[d])
        else:
            rec.update({'flows': None, 'foreign_net_eok': None})
        upsert_daily_record(rec)
    print(f'[fix] 재배치 완료: 이동 {len(moved)}행 / 정리 {len(touched)}행')
    return 0


if __name__ == '__main__':
    sys.exit(main())
