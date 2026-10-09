# -*- coding: utf-8 -*-
"""V4 무결성 검사 — 매 발송 직전 자동 실행 (실패해도 발송은 계속, 해당 항목만 '⚠️ 검증' 줄에 표기).

검사 (사전 고정):
  시점     KR 값은 직전 KR 거래일 P의 확정치인가 (KOSPI parquet·종목 일봉·수급 최신일 = P,
           폭 최종일은 ka10066 확정 대체 여부, 미국 세션은 마감 후 값인가)
  범위     일간 수익률 |r| > 30%, 금리 0~15% 밖, 원달러 800~2,000 밖
  단위     자릿수 범위 — 지수 1,000~20,000pt, 외인 20일 합 |억| 1~200,000, 비율 0~100
  동일값   서로 다른 종목·지표가 원값(float)까지 같으면 재사용 버그 의심
  look-ahead 롤링 계산에 쓴 데이터 최대 날짜 < 판정일(실행일 E)
"""
import numpy as np

import brief_config as cfg


def run_checks(d: dict) -> list:
    sf = d['frame']
    p, e = sf['prev_kr'], sf['exec']
    lim = cfg.RANGE_LIMITS
    fails = []

    # ── 시점 (확정치) ──
    if d['kospi_last'] != p:
        fails.append(f'시점: KOSPI 최신 {d["kospi_last"]} ≠ 직전 거래일 {p}')
    for code, nm in (('005930', '삼전'), ('000660', '하닉')):
        s = d['semi'][code]
        if s['kr_last'] != p:
            fails.append(f'시점: {nm} 일봉 최신 {s["kr_last"]} ≠ {p}')
        if s['frgn_last'] != p:
            fails.append(f'시점: {nm} 수급 최신 {s["frgn_last"]} ≠ {p}')
    if d['breadth'].get('date') != p:
        fails.append(f'시점: 폭 최종일 {d["breadth"].get("date")} ≠ {p}')
    if d['foreign'].get('last_date') != p:
        fails.append('시점: 시장 외인 최종일 불일치')
    for d_us in d.get('sessions_used', []):
        if d_us > sf['d_last']:
            fails.append(f'시점: 미마감 미국 세션 {d_us} 사용')

    # ── 범위 ──
    for code in ('005930', '000660'):
        x = d['semi'][code]['x']
        if x is not None and abs(x) > lim['daily_ret_abs_max'] * max(1, len(sf['closed'])):
            fails.append(f'범위: {code} 가중 US 수익률 {x:+.1f}%')
    if not (lim['rate_min'] <= d['us10y']['last'] <= lim['rate_max']):
        fails.append(f'범위: 미10Y {d["us10y"]["last"]}')
    if not (lim['usdkrw_min'] <= d['usdkrw']['last'] <= lim['usdkrw_max']):
        fails.append(f'범위: 원달러 {d["usdkrw"]["last"]}')

    # ── 단위 (자릿수) ──
    if not (0 < d['breadth'].get('ad_ratio20', 1) < 10):
        fails.append(f"단위: A/D 20일 비율 {d['breadth'].get('ad_ratio20')}")
    if not (0 <= d['breadth']['pct_above200'] <= 100):
        fails.append('단위: 200일선 위 비율')
    if not (0 < d['atr']['pct'] < 20):
        fails.append(f'단위: ATR% {d["atr"]["pct"]:.2f} (이중 % 변환 의심)')
    for code in ('005930', '000660'):
        v = abs(d['semi'][code]['frgn20'])
        if v and not (1 <= v <= 200_000):
            fails.append(f'단위: {code} 외인 20일 {v:,.0f}억 (원/억/조 혼동 의심)')
    if d['regime'].get('dd') is not None and not (-100 < d['regime']['dd'] <= 0):
        fails.append('단위: 고점 대비 낙폭')

    # ── 동일값 (재사용 버그) ──
    sam, hyn = d['semi']['005930'], d['semi']['000660']
    for key in ('x', 'ma60', 'ma200', 'frgn20', 'exp_gap'):
        a, b = sam.get(key), hyn.get(key)
        if a is not None and b is not None and a == b and a != 0:
            fails.append(f'동일값: 삼전·하닉 {key} = {a}')

    # ── look-ahead ──
    for code in ('005930', '000660'):
        if d['semi'][code].get('beta_max_date') and d['semi'][code]['beta_max_date'] >= e:
            fails.append(f'look-ahead: {code} β 데이터 {d["semi"][code]["beta_max_date"]} ≥ {e}')
    if d['breadth'].get('date') and d['breadth']['date'] >= e:
        fails.append('look-ahead: 폭 데이터가 실행일 이후')
    return fails
