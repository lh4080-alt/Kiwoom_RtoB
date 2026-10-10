# -*- coding: utf-8 -*-
"""원천별 금액 단위 — 단일 선언 (2026-10-10, ka10051 천만원/억원 착오 재발 방지).

모든 변환은 이 표를 거친다. 단위는 tests/test_units.py가 실제 날짜 원값으로 교차 검증한다.
"""
UNIT_TO_EOK = {          # 원값 × 계수 = 억원
    'ka10051': 1.0,          # 업종별투자자순매수 (amt_qty_tp=0) — 억원
    'ka10059_amt': 0.01,     # 종목별투자자기관별 금액 (amt_qty_tp=1) — 백만원
    'ka10066_amt': 0.01,     # 장마감후투자자별매매 금액 (amt_qty_tp=1) — 백만원
    'ka20006_tv': 0.01,      # 업종일봉 trde_prica — 백만원
    'ka10081_tv': 0.01,      # 주식일봉 trde_prica — 백만원
    'naver_index_trend': 1.0,   # m.stock.naver.com /api/index/KOSPI/trend — 억원
}


def to_eok(raw, source: str) -> float:
    s = str(raw).replace(',', '').replace('+', '')
    if s.startswith('--'):
        s = '-' + s[2:]
    return float(s) * UNIT_TO_EOK[source]


def to_mil(raw, source: str) -> float:
    """백만원 — flows3 패널 공통 단위."""
    return to_eok(raw, source) * 100
