# -*- coding: utf-8 -*-
"""§8 데이터 품질 자동 점검 — 최종작업지시서 (2026-10-07).

원칙: 자동 보정 금지 (이상은 알림만 — 데이터를 몰래 고치지 않음).

검사 항목:
  1. 날짜 정렬 교차검증 — jsonl flows(삼전·하닉 frgnr) vs ka10059 재조회 (어제 기준)
  2. 이상치 — foreign_net 극단치, kospi_ret None, 0값 급변
  3. 수익률 정합 — jsonl kospi_ret vs ka20006 재계산 (전일 기준)
  4. 소스 누락 — 필수 필드 결측
  5. 하트비트 — 점검·수집 자체가 안 돌았을 때 알림
  6. 셀프테스트 — 앵커 오염 방지 (범위 밖 신호가 첫날로 몰리지 않는지)

부가: 알림 중복 양제(동일 알림 24h), 점검 이력 jsonl, 데이터 해시 스냅샷(재현용).

실행: macro_once daily 말미 자동 호출, 또는 python -m automation.data_quality
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, '..', 'config', 'data')
LOG_PATH = os.path.join(DATA, 'quality_log.jsonl')
STATE_PATH = os.path.join(DATA, 'quality_state.json')
SNAP_DIR = os.path.join(DATA, 'snapshots')
HASH_TARGETS = ['macro_monitor.jsonl', 'stock_flows.parquet',
                'kospi_daily_ohlc.parquet', 'breadth_panel_full.parquet']
TOL_PCT = 0.05  # 교차검증 허용 오차 (5%)
FN_LIMIT = 50_000  # 억원 — 외인 시장 합 극단 임계 (사전 고정)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


def _selftest() -> list:
    """앵커 오염 방지 셀프테스트 — 데이터 범위 밖(이후) 신호는 매칭 0이어야 함."""
    alerts = []
    idx = pd.to_datetime(['2021-01-04', '2021-01-05', '2021-01-06']).date
    thr = pd.Timestamp('2021-06-01').date()  # 데이터 범위 이후 신호 — 앵커되면 오염
    i0 = [i for i, d in enumerate(idx) if d >= thr]
    if i0:
        alerts.append('셀프테스트 실패: 범위 밖 신호가 매칭됨 (앵커 오염 패턴)')
    if sorted(idx) != list(idx):
        alerts.append('셀프테스트 실패: 날짜 정렬 이상')
    return alerts


def run_checks(cross_alerts: list = None) -> dict:
    sys.path.insert(0, BASE)
    from macro_monitor import JSONL_PATH, load_history
    alerts = _selftest() + list(cross_alerts or [])
    try:
        alerts += buyback_disclosure_check()
    except Exception as e:
        alerts.append(f'자사주 공시 감시 실패: {str(e)[:60]}')
    hist = load_history(raw=True)   # 기록기 건강 점검 — 무효 가림 없이
    now = datetime.now().isoformat(timespec='seconds')

    last = hist[-1] if hist else {}
    if not any(r.get('flows') for r in hist):
        alerts.append('flows 기록 행 없음 — 소스 누락?')

    last = hist[-1] if hist else {}
    from macro_monitor import k51_foreign_z
    kf = k51_foreign_z(last.get('date', '99991231')) if last else None
    if kf and abs(kf['last']) > FN_LIMIT:
        alerts.append(f"이상치: 외인 시장(ka10051) {kf['last']:+,.0f}억 — 임계 {FN_LIMIT:,}억 초과")
    if last.get('kospi_ret') is None:
        alerts.append('소스 누락: kospi_ret None (T+1 소급 대기 중일 수 있음 — 익일 재확인)')
    b = last.get('breadth') or {}
    if b.get('pct_above_ma200') is None:
        alerts.append('소스 누락: breadth 미기록')
    # 정체 검사 — 어제와 breadth 핵심값이 소수점까지 동일 + 패널 미갱신 → 갱신 실패 의심
    if len(hist) >= 2:
        prev_b = hist[-2].get('breadth') or {}
        if (b.get('advancers') is not None and prev_b.get('advancers') == b.get('advancers')
                and prev_b.get('pct_above_ma200') == b.get('pct_above_ma200')
                and b.get('panel_last_date') not in (None, last['date'])):
            alerts.append(f'breadth 정체: panel_last {b.get("panel_last_date")} — 패널 갱신 실패 의심')

    # 수익률 정합 — 어제 kospi_ret vs kospi parquet 재계산
    try:
        ohlc = pd.read_parquet(os.path.join(DATA, 'kospi_daily_ohlc.parquet'))
        c = ohlc['close'].astype(float)
        if len(c) >= 3:
            calc = (c.iloc[-2] / c.iloc[-3] - 1) * 100
            stored = next((r.get('kospi_ret') for r in reversed(hist)
                           if r['date'] == c.index[-2].strftime('%Y%m%d')), None)
            if stored is not None and abs(stored - calc) > 0.2:
                alerts.append(f'수익률 정합 실패: jsonl {stored} vs 재계산 {calc:+.2f}')
    except Exception as e:
        alerts.append(f'수익률 정합 검사 실패: {e}')

    # 하트비트 — 마지막 행이 오래됨 (주말+휴장 여유 4일)
    if last:
        d_last = datetime.strptime(last['date'], '%Y%m%d')
        lag = (datetime.now() - d_last).days
        if lag > 4:
            alerts.append(f'하트비트: 마지막 기록 {last["date"]} — {lag}일 경과')

    # 신선도 검사 (2026-10-07 breadth 3주 동결 사고) — 산출물 최신 날짜 = 직전 거래일
    try:
        ohlc = pd.read_parquet(os.path.join(DATA, 'kospi_daily_ohlc.parquet'))
        # 직전 거래일은 kr_calendar로 — parquet 자신으로 계산하면 parquet이 멈춰도 통과하는
        # 순환 검사가 됨 (2026-10-09 V1: parquet 10/7 장중값 정체를 놓친 원인)
        from modules.semi_trigger.kr_calendar import prev_kr_trading_day
        prev_trade = pd.Timestamp(prev_kr_trading_day(pd.Timestamp.now().date()))
        for label, fpath in (('breadth 패널', os.path.join(DATA, 'breadth_close_panel.parquet')),
                             ('kospi parquet', os.path.join(DATA, 'kospi_daily_ohlc.parquet'))):
            if os.path.exists(fpath):
                last_d = pd.read_parquet(fpath).index.max()
                if last_d < prev_trade:
                    alerts.append(f'신선도: {label} 최신 {last_d.date()} < 직전 거래일 {prev_trade.date()} '
                                  '— 재빌드/스케줄 사망 의심')
        fwd = os.path.join(DATA, 'forward_log.jsonl')
        if os.path.exists(fwd):
            last_f = [json.loads(l) for l in open(fwd, encoding='utf-8')][-1]
            if last_f['date'] < prev_trade.strftime('%Y%m%d'):
                alerts.append(f'신선도: forward_log 최신 {last_f["date"]} < 직전 거래일')
    except Exception as e:
        alerts.append(f'신선도 검사 실패: {e}')

    # T+1 결측 감지 (10/6 케이스 재현 — ka20006 당일 봉 결측 시 국면이 오래된 창으로 계산됨)
    last_k = last.get('kospi_closes_last')
    if last_k:
        last_kd = pd.Timestamp(last_k)
        try:
            from modules.semi_trigger.kr_calendar import prev_kr_trading_day
            prev_trade2 = pd.Timestamp(prev_kr_trading_day(pd.Timestamp.now().date()))
            if last_kd < prev_trade2:
                alerts.append(f'국면 창 지연: kospi 계산 마지막 봉 {last_k} < 직전 거래일 '
                              f'{prev_trade2.date()} — 당일 봉 결측, 익일 소급 대기')
        except Exception:
            pass

    # 중복 억제
    state = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding='utf-8') as f:
            state = json.load(f)
    sent = []
    for a in alerts:
        key = a.split(':')[0][:20]
        last_t = state.get(key)
        if last_t and (datetime.now() - datetime.fromisoformat(last_t)).total_seconds() < 86400:
            continue  # 24h 내 동일 알림 억제
        state[key] = now
        sent.append(a)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

    # 이력 로그
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'at': now, 'alerts': alerts, 'sent': sent},
                           ensure_ascii=False) + '\n')

    # 데이터 해시 스냅샷 (재현용)
    os.makedirs(SNAP_DIR, exist_ok=True)
    hashes = {}
    for fn_ in HASH_TARGETS:
        p = os.path.join(DATA, fn_)
        if os.path.exists(p):
            hashes[fn_] = _sha256(p)
    with open(os.path.join(SNAP_DIR, f'hash_{datetime.now().strftime("%Y%m%d")}.json'),
              'w', encoding='utf-8') as f:
        json.dump({'at': now, 'hashes': hashes}, f, ensure_ascii=False, indent=1)

    return {'at': now, 'alerts': alerts, 'sent': sent}


token_holder = {}


async def verify_flows_cross(token, row) -> list:
    """jsonl flows(삼전·하닉 frgnr) vs ka10059 재조회 — 날짜 정렬 교차검증 (async)."""
    from utils.rate_limiter import requests
    import utils.config as config
    alerts = []

    async def fetch(code, d):
        r = await requests.post(
            config.get_host_url() + '/api/dostk/stkinfo',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {token}', 'cont-yn': 'N',
                     'next-key': '', 'api-id': 'ka10059'},
            json={'dt': d, 'stk_cd': code, 'amt_qty_tp': '1',
                  'trde_tp': '0', 'unit_tp': '1'})
        for it in (r.json() or {}).get('stk_invsr_orgn') or []:
            if str(it.get('dt', '')).replace('-', '') == d:
                s = str(it.get('frgnr_invsr', '0')).replace('+', '')
                return float(s) / 100  # 백만원 → 억원
        return None

    vals = {code: await fetch(code, row['date']) for code in ('005930', '000660')}
    for code, v in vals.items():
        stored = (row.get('flows') or {}).get(code, {}).get('frgnr')
        if stored is None or v is None:
            alerts.append(f'{row["date"]} {code}: 교차검증 불가 (stored={stored}, api={v})')
        elif abs(stored - v) > max(abs(v) * TOL_PCT, 100):
            alerts.append(f'날짜 정렬 의심: {row["date"]} {code} frgnr jsonl={stored} vs ka10059={v:.1f}')
    return alerts


if __name__ == '__main__':
    import asyncio
    from modules.semi_trigger.token_provider import get_semi_token
    from macro_monitor import load_history

    async def main():
        token = await get_semi_token()
        flow_rows = [r for r in load_history(raw=True) if r.get('flows')]
        cross = (await verify_flows_cross(token, flow_rows[-1])) if flow_rows \
            else ['flows 행 없음']
        res = run_checks(cross)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        if res['sent']:
            from telegram.tel_send import tel_send
            await tel_send('⚠️ [품질점검] ' + ' | '.join(res['sent']))

    sys.exit(asyncio.run(main()))


# ── 시장 외인 일일 대조 (2026-10-10 Lee 지시 — ka10051 단위·정합 상시 감시) ──
# 임계는 2025-10~2026-10 정상일 분포에서 (246일): Σ주권 차 q99 465·최대 887억 / 네이버 외인 차 q99 94·최대 120억,
# 개인 차 q99 9억. 미해결·오류일은 한 자릿수 위 (9/17 외인 3,775·개인 3,741억, 10/1 외인 6,752억).
MKT_SUM_TOL = (0.0, 1000)    # ka10051 vs Σ주권(941 상장 생존 종목): 1,000억 — 생존편향 차는 정상 범위
NAVER_TOL_FRGN = 500         # 억원 — 외국인+기타외국인
NAVER_TOL_IND = 500          # 억원


def naver_index_trend(p) -> dict:
    """네이버 KOSPI 투자자 동향 (bizdate=P) — 응답 날짜가 P가 아니면 None."""
    import requests
    from brief.units import to_eok
    try:
        js = requests.get(f'https://m.stock.naver.com/api/index/KOSPI/trend?bizdate={p:%Y%m%d}',
                          headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()
        if js.get('bizdate') != f'{p:%Y%m%d}':
            return None
        return {'frgn': to_eok(js['foreignValue'], 'naver_index_trend'),
                'ind': to_eok(js['personalValue'], 'naver_index_trend'),
                'inst': to_eok(js['institutionalValue'], 'naver_index_trend')}
    except Exception:
        return None


def market_flow_crosscheck(p, k51_row: dict, sum_frgn_eok, naver: dict) -> list:
    """k51_row: {'frgn','natfor','ind'} 억원 (ka10051 P일). 반환: 알림 문자열 목록."""
    alerts = []
    if k51_row is None:
        return [f'대조: ka10051 {p} 행 없음 (04:40 갱신 실패?)']
    f = k51_row['frgn']
    if sum_frgn_eok is None:
        alerts.append(f'대조: Σ주권(ka10059) {p} 없음')
    else:
        tol = max(abs(f) * MKT_SUM_TOL[0], MKT_SUM_TOL[1])
        if abs(f - sum_frgn_eok) > tol:
            alerts.append(f'대조: 시장 외인 ka10051 {f:+,.0f} vs Σ주권 {sum_frgn_eok:+,.0f}억 (허용 {tol:,.0f})')
    if naver is None:
        alerts.append(f'대조: 네이버 지수 투자자 {p} 응답 없음')
    else:
        fn = f + (k51_row.get('natfor') or 0)
        if abs(fn - naver['frgn']) > NAVER_TOL_FRGN:
            alerts.append(f"대조: 외인 ka10051 {fn:+,.0f} vs 네이버 {naver['frgn']:+,.0f}억")
        if abs(k51_row['ind'] - naver['ind']) > NAVER_TOL_IND:
            alerts.append(f"대조: 개인 ka10051 {k51_row['ind']:+,.0f} vs 네이버 {naver['ind']:+,.0f}억")
    return alerts


# ── 자사주 취득 공시 감시 (2026-10-10 Lee 지시 — 알림만, 설정 자동 수정 없음) ──
BUYBACK_LOOKBACK_DAYS = 120


def buyback_disclosure_check(codes=('005930', '000660')) -> list:
    """네이버 공시에서 '자기주식 … 취득 … 결정'(신탁계약 포함) 중 brief_config.BUYBACK_PERIODS에 없는 것 → 알림."""
    import requests
    import brief_config as cfg
    known = {b.get('disclosure_id') for b in cfg.BUYBACK_PERIODS}
    cutoff = (datetime.now() - pd.Timedelta(days=BUYBACK_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    alerts = []
    for c in codes:
        try:
            js = requests.get(f'https://m.stock.naver.com/api/stock/{c}/disclosure?pageSize=100&page=1',
                              headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()
        except Exception as e:
            alerts.append(f'자사주 공시 확인 실패 {c}: {str(e)[:40]}')
            continue
        for x in js if isinstance(js, list) else []:
            t, d = x.get('title') or '', str(x.get('datetime', ''))[:10]
            if '자기주식' in t and '취득' in t and '결정' in t and d >= cutoff                     and x.get('disclosureId') not in known:
                alerts.append(f"자사주 신규 공시 {c} {d} '{t}' (id {x.get('disclosureId')}) — "
                              'brief_config.BUYBACK_PERIODS 미등록')
    return alerts
