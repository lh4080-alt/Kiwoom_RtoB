# -*- coding: utf-8 -*-
"""ka10066 날짜 판정 고정 테스트 (2026-10-10 — 응답에 날짜가 없어 종가 대조로 판정)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))

from macro_monitor import date_by_price  # noqa: E402

CL = {'005930': {'20261007': 269000, '20261008': 263000},
      '000660': {'20261007': 1715000, '20261008': 1686000}}
CODES = ['005930', '000660', '455850']
CAND = ['20261008', '20261007']


def test_prev_day_snapshot():      # 16:50 — 전일분이 온 경우
    assert date_by_price({'005930': 269000, '000660': 1715000}, CL, CODES, CAND) == '20261007'


def test_same_day_snapshot():      # 20:30 — 당일분이 온 경우
    assert date_by_price({'005930': 263000, '000660': 1686000}, CL, CODES, CAND) == '20261008'


def test_mismatch_or_too_few():    # 미확정 종가·종목 부족 → 기록 안 함
    assert date_by_price({'005930': 263500, '000660': 1686000}, CL, CODES, CAND) is None
    assert date_by_price({'005930': 263000}, CL, CODES, CAND) is None


def test_ambiguous():              # 두 날 종가가 같으면 판정 불가
    cl = {'005930': {'a': 1, 'b': 1}, '000660': {'a': 2, 'b': 2}}
    assert date_by_price({'005930': 1, '000660': 2}, cl, CODES, ['a', 'b']) is None
