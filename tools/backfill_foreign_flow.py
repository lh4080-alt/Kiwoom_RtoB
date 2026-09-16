# -*- coding: utf-8 -*-
"""외국인 시장 순매수 백필 — 최근 N거래일을 ka10058(일별×시장×외국인)로 소급해
macro_monitor.jsonl 행에 병합(foreign_net_eok). 작업 호스팅 실행 전용 (장시간 소요)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.semi_trigger.token_provider import get_semi_token
from foreign_flow import fetch_foreign_market_net
from macro_monitor import upsert_daily_record, load_history


async def main(days: int = 70):
    token = await get_semi_token(force=True)
    hist = load_history()
    # 거래일 기준 = 패널(지수 OHLC) 최신 70거래일, 최근부터 소급
    dates = sorted({r['date'] for r in hist})[-days:]
    done = 0
    for d in dates:
        try:
            net = await fetch_foreign_market_net(token, d)
        except Exception as e:
            print(f'{d} 실패: {e}', flush=True)
            continue
        if net is None:
            print(f'{d} 데이터 없음 — 스킵', flush=True)
            continue
        upsert_daily_record({'date': d, 'foreign_net_eok': net})
        done += 1
        print(f'{d}: 외인 순매수 {net:+,.0f}억 (누적 {done}/{len(dates)})', flush=True)
        await asyncio.sleep(0.3)
    print(f'백필 완료 {done}/{len(dates)}일', flush=True)


if __name__ == '__main__':
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 70
    asyncio.run(main(days))
