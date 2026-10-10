# -*- coding: utf-8 -*-
"""원천별 단위 고정 — 실제 날짜(2026-10-08) 원값으로 교차 원천 일치 확인 (ka10051 단위 착오 재발 방지)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))
from brief.units import UNIT_TO_EOK, to_eok, to_mil  # noqa: E402

FX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures',
                                 'units_20261008.json'), encoding='utf-8'))


def test_ka10051_is_eok_matches_stock_sum():
    """시장 외인: ka10051(억원) = Σ ka10066 주권(백만원) — 1억 이내."""
    a = to_eok(FX['ka10051_frgnr_raw'], 'ka10051')
    b = to_eok(FX['ka10066_kospi_stocks_frgnr_sum_raw'], 'ka10066_amt')
    assert abs(a - b) < 1, (a, b)
    # 천만원 가정(÷10)이면 10배 어긋나야 한다 — 2026-10-07 착오 재현 방지
    assert abs(a / 10 - b) > 1000


def test_ka10051_vs_naver_index():
    """네이버 지수 투자자 동향 외인 = 외국인 + 기타외국인 (억원), 개인 일치."""
    f = to_eok(FX['ka10051_frgnr_raw'], 'ka10051') + to_eok(FX['ka10051_natfor_raw'], 'ka10051')
    assert abs(f - to_eok(FX['naver_foreign_raw'], 'naver_index_trend')) <= 100
    assert to_eok(FX['ka10051_ind_raw'], 'ka10051') == to_eok(FX['naver_personal_raw'], 'naver_index_trend')


def test_ka10059_amount_and_tv_mil():
    assert abs(to_eok(FX['ka10059_005930_frgnr_raw'], 'ka10059_amt') - FX['stock_flows_005930_frgnr_eok']) < 0.01
    assert abs(to_eok(FX['ka10059_005930_tv_raw'], 'ka10059_amt') - FX['ka10081_005930_tv_eok']) < 0.01


def test_ka20006_tv_scale():
    """KOSPI 일 거래대금 — 백만원 → 억원이면 수십만 억원(수십 조) 범위."""
    tv = to_eok(FX['ka20006_trde_prica_raw'], 'ka20006_tv')
    assert 50_000 < tv < 2_000_000
    assert to_eok(FX['ka10059_005930_tv_raw'], 'ka10059_amt') < tv   # 삼전 < 시장


def test_flows3_market_unit_consistent():
    """flows3.load_market의 ka10051 → 백만원 계수가 단위 표와 같아야 한다."""
    import inspect
    from brief import flows3
    assert to_mil(1, 'ka10051') == 100
    assert "m[c].values * 100" in inspect.getsource(flows3.load_market)
    assert set(UNIT_TO_EOK) >= {'ka10051', 'ka10059_amt', 'ka10066_amt', 'ka20006_tv'}
