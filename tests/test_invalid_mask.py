# -*- coding: utf-8 -*-
"""무효 플래그 가림 고정 테스트 (2026-10-10 Lee 지시 — 플래그 값은 어떤 계산에서도 읽지 않음).

load_history()는 invalid[필드]가 붙은 값을 None으로 돌려주고, 디스크 원본은 보존한다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))

from macro_monitor import load_history, upsert_daily_record


def _write(p, rows):
    with open(p, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def test_flagged_field_masked(tmp_path):
    p = str(tmp_path / 't.jsonl')
    _write(p, [{'date': '20261001', 'kospi_ret': 1.95, 'foreign_net_eok': -4165,
                'semis_detail': {'삼성전자': 1.11}, 'invalid': {'foreign_net_eok': 'x', 'semis_detail': 'y'}}])
    r = load_history(p)[0]
    assert r['foreign_net_eok'] is None and r['semis_detail'] is None
    assert r['kospi_ret'] == 1.95            # 플래그 없는 필드는 그대로


def test_raw_and_disk_preserved(tmp_path):
    p = str(tmp_path / 't.jsonl')
    _write(p, [{'date': '20261001', 'foreign_net_eok': -4165, 'invalid': {'foreign_net_eok': 'x'}}])
    assert load_history(p, raw=True)[0]['foreign_net_eok'] == -4165
    upsert_daily_record({'date': '20261001', 'kospi_ret': 1.95}, path=p)   # 쓰기 경로는 원본 기준
    raw = json.loads(open(p, encoding='utf-8').readline())
    assert raw['foreign_net_eok'] == -4165 and raw['invalid'] == {'foreign_net_eok': 'x'}


def test_upsert_unions_invalid(tmp_path):
    p = str(tmp_path / 't.jsonl')
    _write(p, [{'date': '20261001', 'semis_ret': 1.0, 'invalid': {'foreign_net_eok': 'x'}}])
    upsert_daily_record({'date': '20261001', 'invalid': {'semis_ret': 'y'}}, path=p)
    raw = json.loads(open(p, encoding='utf-8').readline())
    assert raw['invalid'] == {'foreign_net_eok': 'x', 'semis_ret': 'y'}
    assert load_history(p)[0]['semis_ret'] is None
