# -*- coding: utf-8 -*-
"""KRX 거래일 캘린더 — semi_trigger 신호 평가 기준일 정확화 (지시서 작업 1).

과거: kospi_daily_ohlc.parquet의 실측 거래일 (가장 정확 — ka20006 소스).
미래: 공휴일 하드코딩 리스트 (연 단위, 지시서 허용) + 주말 규칙.

함수:
  is_kr_trading_day(date)      → bool
  next_kr_trading_day(date)    → 다음 거래일 (date 자신 제외)
  prev_kr_trading_day(date)    → 이전 거래일 (date 자신 제외)

단위테스트: tests/test_kr_calendar.py (한글날·추석 연휴·12/31·주말)
"""
import os
from datetime import date, timedelta

import pandas as pd

_PARQUET = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), 'config', 'data',
    'kospi_daily_ohlc.parquet')

# 미래(또는 parquet 미커버) 날짜용 공휴일 — KRX 휴장일 (연 단위 하드코딩 허용, 지시서)
# 2026 검증: 9/24~25 추석 휴장(9/26 토), 9/28은 거래일, 10/3 토+10/5 대체공휴일, 10/9 한글날 (ka20006 실측)
KR_HOLIDAYS = {
    2026: ['01-01', '03-02', '05-05', '05-25', '06-08', '08-17',
           '09-24', '09-25', '10-05', '10-09', '12-25'],
    2027: ['01-01'],
}

_cache = None


def _kr_trading_days() -> set:
    global _cache
    if _cache is None:
        try:
            df = pd.read_parquet(_PARQUET)
            _cache = {d.date().isoformat() for d in df.index}
        except Exception:
            _cache = set()
    return _cache


def to_date(d) -> date:
    if isinstance(d, date):
        return d
    return pd.Timestamp(d).date()


def is_kr_trading_day(d) -> bool:
    d = to_date(d)
    iso = d.isoformat()
    days = _kr_trading_days()
    if days:  # parquet 커버 구간 (과거+당일까지 수집분) — 실측 우선
        if iso in days:
            return True
        # parquet이 커버하는 연도+오늘 이전이면 실측 없음 = 휴장
        if iso <= max(days):
            return False
    # 미래 — 하드코딩 공휴일 + 주말
    if d.weekday() >= 5:
        return False
    return f'{d.month:02d}-{d.day:02d}' not in KR_HOLIDAYS.get(d.year, [])


def next_kr_trading_day(d) -> str:
    """date보다 늦은 최초 KR 거래일 (ISO 문자열)."""
    cur = to_date(d) + timedelta(days=1)
    for _ in range(60):
        if is_kr_trading_day(cur):
            return cur.isoformat()
        cur += timedelta(days=1)
    return None


def prev_kr_trading_day(d) -> str:
    cur = to_date(d) - timedelta(days=1)
    for _ in range(60):
        if is_kr_trading_day(cur):
            return cur.isoformat()
        cur -= timedelta(days=1)
    return None
