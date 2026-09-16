"""KOSPI 시장 전체 외국인 순매수 (ka10058 일별×시장×투자자) — 2026-09-16.

배경: 시장 전체 외국인 일별 순매수 시계열의 직접 TR이 키움 REST에 없음
(ka10066=당일 종목별 스냅샷, pykrx KRX 수집 사망). ka10058(투자자별 일별 매매,
strt~end×mrkt_tp×invsr_tp)은 시장+투자자+기간 조회가 가능해, 종목별 순매수를
페이지 합산하면 시장 전체 당일 외국인 순매수(만원 단위 → 억원 변환)가 된다.

단위 검증(2종목 교차): netslmt_amt × 100,000 = 원 (즉 amt/1000 = 억원).
"""
import asyncio

import utils.config as config


async def fetch_foreign_market_net(token: str, d: str, max_pages: int = 40) -> float:
    """당일(d=YYYYMMDD) 코스피 시장 전체 외국인 순매수 합계 (억원). 실패 시 None.

    ka10058: strt=end=d, mrkt_tp=001(KOSPI), invsr_tp=9000(외국인),
             trde_tp=0(순매수), stex_tp=1(KRX). 페이지 전체 합산.
    """
    from utils.rate_limiter import requests

    total = 0
    rows_seen = 0
    cont, nk = 'N', ''
    for _page in range(max_pages):
        r = await requests.post(
            config.get_host_url() + '/api/dostk/stkinfo',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {token}', 'cont-yn': cont,
                     'next-key': nk, 'api-id': 'ka10058'},
            json={'strt_dt': d, 'end_dt': d, 'trde_tp': '0',
                  'mrkt_tp': '001', 'invsr_tp': '9000', 'stex_tp': '1'})
        data = r.json()
        body = data.get('invsr_daly_trde_stk') or []
        for it in body:
            s = str(it.get('netslmt_amt', '0')).replace('+', '')
            if s.startswith('--'):
                s = '-' + s[2:]
            try:
                total += int(float(s))
            except (ValueError, TypeError):
                pass
        rows_seen += len(body)
        cont = r.headers.get('cont-yn', 'N')
        nk = r.headers.get('next-key', '')
        if cont != 'Y':
            break
        await asyncio.sleep(0.15)
    return round(total / 1000, 1) if rows_seen else None  # 십만원 → 억원
