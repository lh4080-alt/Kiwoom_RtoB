# -*- coding: utf-8 -*-
"""거시 모니터 원샷 러너 — semi_once.py와 같은 패턴 (데몬 아님, 단발 실행).

스케줄(작업스케줄러):
  RtoB_Macro_Daily   평일 15:50  python macro_once.py daily

modes:
  daily                당일 관찰 기록 + 텔레그램 발송
  backfill [N일]       과거 N일(기본 100) 국내 일봉으로 기록 소급 (주말 스킵) — 초기 셋업용
  report               마지막 기록 재발송 (디버그)

토큰: semi 전용 -XMf61 (GDLLsq=KB 매매봇 키와 격리 — token_provider 참고).
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def run_daily(send_telegram: bool = True) -> int:
    from modules.semi_trigger.token_provider import get_semi_token
    from macro_monitor import run_daily as collect, format_report
    token = await get_semi_token()
    if not token:
        from telegram.tel_send import tel_send
        await tel_send("⚠️ [macro] 토큰 발급 실패 — 수집 스킵")
        return 1
    record = await collect(token)
    if not record:
        print(f"[macro_once] 국내 휴장 — 스킵 {datetime.now().isoformat(timespec='seconds')}")
        return 0
    missing = [k for k in ('semis_ret', 'others_ret', 'kospi_ret') if record.get(k) is None]
    if len(missing) == len(['semis_ret', 'others_ret', 'kospi_ret']):
        from telegram.tel_send import tel_send
        await tel_send("⚠️ [macro] 국내 데이터 전멸 — 오늘 기록 스킵 (토큰/API 확인)")
        return 1
    if send_telegram:
        from telegram.tel_send import tel_send
        await tel_send(format_report(record))
        # 대시보드 HTML 문서 첨부
        try:
            import httpx
            dash_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     'tools', 'macro_dashboard.html')
            if os.path.exists(dash_path):
                tok = open(r'C:\market_data_collector\config\telegram_token.txt', encoding='utf-8-sig').read().strip()
                chat = open(r'C:\market_data_collector\config\telegram_chat_id.txt', encoding='utf-8-sig').read().strip()
                async with httpx.AsyncClient(timeout=30) as hc:
                    with open(dash_path, 'rb') as f:
                        r = await hc.post(
                            f'https://api.telegram.org/bot{tok}/sendDocument',
                            data={'chat_id': chat},
                            files={'document': ('macro_dashboard.html', f, 'text/html')})
        except Exception:
            pass  # 대시보드 첨부 실패해도 리포트는 이미 발송됨
    print(f"[macro_once] daily 완료 {datetime.now().isoformat(timespec='seconds')} "
          f"missing={missing or 'none'}")
    return 0


async def run_backfill(days: int = 100, send_telegram: bool = False) -> int:
    """과거 소급 — 국내 일봉(ka10081 100일) + yfinance로 매일 행 생성."""
    from modules.semi_trigger.token_provider import get_semi_token
    from macro_monitor import (fetch_kr_daily_closes, fetch_macro_histories,
                               _daily_returns, upsert_daily_record,
                               overnight_session_return, SEMIS, OTHER_ETFS,
                               SYM_NQ, SYM_USDKRW, SYM_US10Y, SYM_KOSPI)
    token = await get_semi_token()
    if not token:
        print("[macro] 토큰 발급 실패")
        return 1
    today = date.today()
    base_dt = today.strftime('%Y%m%d')
    kr = await fetch_kr_daily_closes(token, base_dt, SEMIS + OTHER_ETFS)
    macro = await asyncio.to_thread(fetch_macro_histories, days / 240.0)

    semis_ret = {c: _daily_returns(kr[c]) for c in kr if c in [s[0] for s in SEMIS]}
    others_ret = {c: _daily_returns(kr[c]) for c in kr if c in [s[0] for s in OTHER_ETFS]}

    def block_avg(rets, d):
        vals = [r.get(d) for r in rets.values() if r.get(d) is not None]
        return (sum(vals) / len(vals)) if vals else None

    # 국내 일봉에 존재하는 날짜만 (거래일 자동 반영) — 최근 days일
    all_dates = sorted({d for r in list(semis_ret.values()) + list(others_ret.values())
                        for d in r.keys()})[-days:]

    n_saved = 0
    for dstr in all_dates:
        semis_t = block_avg(semis_ret, dstr)
        others_t = block_avg(others_ret, dstr)
        d = datetime.strptime(dstr, '%Y%m%d')
        record = {
            'date': dstr,
            'generated_at': datetime.now().isoformat(timespec='seconds'),
            'kospi_ret': None,
            'semis_ret': round(semis_t, 2) if semis_t is not None else None,
            'semis_detail': {name: round(r[dstr], 2) for code, name in SEMIS
                             if (r := semis_ret.get(code)) and r.get(dstr) is not None},
            'others_ret': round(others_t, 2) if others_t is not None else None,
            'others_detail': {name: round(r[dstr], 2) for code, name in OTHER_ETFS
                              if (r := others_ret.get(code)) and r.get(dstr) is not None},
            'rotation_spread': (round(semis_t - others_t, 2)
                                if semis_t is not None and others_t is not None else None),
            'nq_overnight': None, 'usdkrw': None, 'usdkrw_ret': None, 'us10y': None,
        }
        nq_r = overnight_session_return(macro.get(SYM_NQ, {}), dstr)
        record['nq_overnight'] = round(nq_r, 2) if nq_r is not None else None
        # yfinance는 장중/장전에도 '오늘' 행을 placeholder로 돌려줌 → 과거 날짜만 채움.
        # 오늘 행은 15:50 daily가 최종값으로 upsert한다.
        iso = d.strftime('%Y-%m-%d')
        is_past = iso < date.today().strftime('%Y-%m-%d')
        if is_past:
            usd = macro.get(SYM_USDKRW, {})
            if iso in usd:
                record['usdkrw'] = round(usd[iso], 2)
                prev_iso = (d - timedelta(days=1)).strftime('%Y-%m-%d')
                if prev_iso in usd and usd[prev_iso] > 0:
                    record['usdkrw_ret'] = round((usd[iso] / usd[prev_iso] - 1) * 100.0, 2)
            us10y = macro.get(SYM_US10Y, {})
            if iso in us10y:
                record['us10y'] = round(us10y[iso], 2)
            else:
                # TNX 하루 결측 폴백 — 과거 최근값 (최대 3일)
                for back in range(1, 4):
                    cand = (d - timedelta(days=back)).strftime('%Y-%m-%d')
                    if cand in us10y:
                        record['us10y'] = round(us10y[cand], 2)
                        break
            kospi = macro.get(SYM_KOSPI, {})
            prev = (d - timedelta(days=1)).strftime('%Y-%m-%d')
            if iso in kospi and prev in kospi and kospi[prev] > 0:
                record['kospi_ret'] = round((kospi[iso] / kospi[prev] - 1) * 100.0, 2)
        upsert_daily_record(record)
        n_saved += 1
    print(f"[macro_once] backfill 완료 {n_saved}일 ({all_dates[0]}~{all_dates[-1]})")
    return 0


async def _main_async(mode: str, arg: str) -> int:
    try:
        if mode == 'daily':
            return await run_daily()
        if mode == 'backfill':
            return await run_backfill(int(arg) if arg else 100)
        if mode == 'report':
            from macro_monitor import load_history, format_report
            from telegram.tel_send import tel_send
            hist = load_history()
            if not hist:
                print('[macro_once] 기록 없음')
                return 1
            await tel_send(format_report(hist[-1]))
            return 0
        print('사용법: python macro_once.py [daily | backfill [N] | report]')
        return 2
    finally:
        try:
            from utils.rate_limiter import requests
            await requests.close()
        except Exception:
            pass


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else 'daily'
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    return asyncio.run(_main_async(mode, arg))


if __name__ == '__main__':
    sys.exit(main())
