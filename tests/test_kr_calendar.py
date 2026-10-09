# -*- coding: utf-8 -*-
"""kr_calendar 단위테스트 — 한글날·추석 연휴·12/31·주말 (지시서 완료 조건)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))

from modules.semi_trigger.kr_calendar import (is_kr_trading_day, next_kr_trading_day,
                                              prev_kr_trading_day)


def test_hangul_day_2026():
    """10/9 한글날 = 휴장, 10/12 월요일 = 다음 거래일."""
    assert is_kr_trading_day('2026-10-09') is False
    assert next_kr_trading_day('2026-10-09') == '2026-10-12'


def test_chuseok_2026():
    """추석 연휴 — parquet 실측 기반."""
    assert is_kr_trading_day('2026-09-25') is False  # 추석 당일(금)
    assert is_kr_trading_day('2026-09-26') is False  # 토
    assert is_kr_trading_day('2026-09-28') is True   # ka20006 실측 거래일


def test_year_end():
    """12/31 — 2026년 하드코딩엔 휴장 아님(개장, 2025년까지 관행), 주말이면 휴장."""
    # 2026-12-31은 목요일 — 공휴일 리스트에 없으므로 거래일
    assert is_kr_trading_day('2026-12-31') is True
    assert next_kr_trading_day('2026-12-31') == '2027-01-04'  # 1/1 신정+주말


def test_weekend():
    assert is_kr_trading_day('2026-10-10') is False  # 토
    assert is_kr_trading_day('2026-10-11') is False  # 일
    assert next_kr_trading_day('2026-10-08') == '2026-10-12'  # 금(10/9 한글날) 건너뜀


def test_parquet_backfill_days():
    """과거 실측 — 9/28 거래일이었음 (재배치 데이터로 확인된 사실)."""
    assert is_kr_trading_day('2026-09-28') is True
    assert prev_kr_trading_day('2026-10-06') == '2026-10-02'  # 10/5 대체공휴일 휴장 (ka20006 실측)
