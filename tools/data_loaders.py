"""
공용 데이터 로더 — bar_storage / 수집풀 / 시장지수 접근의 단일 진입점.

- load_today_pool_codes(): config/data/collection_pool.json 에서 종목 코드 리스트
- load_7d_bars(code, end_date): \\\\beelink\\market_data 1분봉 → 7일 일봉 OHLCV
- lookup_close(code, eval_date, offset_bdays): N영업일 후 종가 조회
- load_market_change(eval_date): KOSPI/KOSDAQ 일일 등락률 (구현 대기)

경로 외부화:
    MARKET_DATA_ROOT, COLLECTION_POOL_PATH 환경변수
"""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARKET_DATA_ROOT = Path(os.environ.get('MARKET_DATA_ROOT', r'\\beelink\market_data'))
COLLECTION_POOL = Path(os.environ.get(
    'COLLECTION_POOL_PATH',
    str(PROJECT_ROOT / 'config' / 'data' / 'collection_pool.json'),
))


def load_today_pool_codes() -> list:
    """수집풀 JSON에서 종목 코드 리스트 반환."""
    if not COLLECTION_POOL.exists():
        print(f"[수집풀 없음] {COLLECTION_POOL}")
        return []
    with COLLECTION_POOL.open('r', encoding='utf-8') as f:
        pool = json.load(f)
    if not isinstance(pool, dict):
        return []
    return list(pool.keys())


def load_7d_bars(code: str, end_date=None) -> pd.DataFrame | None:
    """
    종목의 분봉 parquet(월 단위)을 읽어 end_date까지의 최근 7거래일 일봉 OHLCV로 집계.
    데이터 부족 시 None.

    Args:
        code: 6자리 종목코드
        end_date: date | str(YYYY-MM-DD) | None (None이면 오늘)
    """
    if end_date is None:
        end_date = date.today()
    elif isinstance(end_date, str):
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    elif isinstance(end_date, datetime):
        end_date = end_date.date()

    # 최근 2개월 분봉으로 거래일 7일 확보
    cur_month_first = end_date.replace(day=1)
    prev_month_first = (cur_month_first - timedelta(days=1)).replace(day=1)
    months = [prev_month_first, cur_month_first]

    frames = []
    for m in months:
        p = MARKET_DATA_ROOT / 'bars_1m' / 'stocks' / code / f"{m.year}" / f"{m.year}{m.month:02d}.parquet"
        if p.exists():
            try:
                frames.append(pd.read_parquet(p))
            except Exception as e:
                print(f"[load_7d_bars] {code} {p.name} 읽기 실패: {e}")

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True)
    df['dt'] = pd.to_datetime(df['dt'])
    df['date'] = df['dt'].dt.date

    daily = df.groupby('date').agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    ).reset_index()

    daily = daily[daily['date'] <= end_date]
    daily = daily.sort_values('date').reset_index(drop=True)

    if len(daily) < 7:
        return None

    return daily.tail(7).reset_index(drop=True)


def lookup_close(code: str, eval_date: str, offset_bdays: int):
    """
    eval_date 기준 offset_bdays 영업일 후의 종가 조회.
    아직 도래 안한 미래일이거나 데이터 없으면 None.
    """
    target_date = pd.to_datetime(eval_date) + pd.tseries.offsets.BDay(offset_bdays)
    if target_date > pd.Timestamp.now().normalize():
        return None
    bars = load_7d_bars(code, end_date=target_date.strftime('%Y-%m-%d'))
    if bars is None or len(bars) == 0:
        return None
    last = bars.iloc[-1]
    if pd.to_datetime(last['date']).normalize() == target_date.normalize():
        return float(last['close'])
    return None


def load_market_change(eval_date: str) -> tuple:
    """
    KOSPI/KOSDAQ 당일 등락률 반환.

    TODO: 시장지수 로더 wire-up
    후보: C:\\market_data_collector\\api\\index_chart.py (KOSPI '001', KOSDAQ '101' 또는 '201')
    또는 \\beelink\\market_data\\bars_1m\\index\\{code}\\{YYYY}\\{YYYYMM}.parquet 직접 읽기.
    """
    raise NotImplementedError('시장지수 로더 연결 필요 (C:\\market_data_collector\\api\\index_chart.py 참조)')
