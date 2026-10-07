# -*- coding: utf-8 -*-
"""upsert 병합 특성 고정 테스트 — None은 덮어쓰지 않음 (2026-10-07 flows 사고 계기).

현재 설계: upsert_daily_record 병합은 null 아닌 신규값만 덮어씀 → None으로
필드 제거 불가. 이 특성을 테스트로 고정해 "알고만 있던" 상태를 방지.
명시적 제거는 clear_fields 사용.
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))

from macro_monitor import upsert_daily_record, load_history, clear_fields


def test_upsert_none_is_ignored(tmp_path):
    p = str(tmp_path / 't.jsonl')
    upsert_daily_record({'date': '20260101', 'kospi_ret': 1.0, 'flows': {'a': 1}}, path=p)
    # None 시도 — 기존값 유지 (설계 특성 고정)
    upsert_daily_record({'date': '20260101', 'kospi_ret': None, 'flows': None}, path=p)
    rows = load_history(p)
    assert rows[0]['kospi_ret'] == 1.0, 'None이 덮어써짐 — 병합 특성 변경됨 (호출처 전수 점검 필요)'
    assert rows[0]['flows'] == {'a': 1}


def test_clear_fields_removes(tmp_path):
    p = str(tmp_path / 't.jsonl')
    upsert_daily_record({'date': '20260101', 'kospi_ret': 1.0, 'flows': {'a': 1}}, path=p)
    clear_fields('20260101', ['flows'], path=p)
    rows = load_history(p)
    assert rows[0]['flows'] is None
    assert rows[0]['kospi_ret'] == 1.0  # 다른 필드 보존


def test_clear_fields_missing_row(tmp_path):
    p = str(tmp_path / 't.jsonl')
    clear_fields('20260101', ['flows'], path=p)  # 없는 날짜 — 예외 없이 통과
    assert load_history(p) == []
