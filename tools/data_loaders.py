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


def load_today_pool_full() -> dict:
    """수집풀 JSON 전체(코드→엔트리 dict) 반환. hit_count 등 메타데이터 필요한 경우."""
    if not COLLECTION_POOL.exists():
        return {}
    with COLLECTION_POOL.open('r', encoding='utf-8') as f:
        pool = json.load(f)
    return pool if isinstance(pool, dict) else {}


def lookup_history(code: str, days: int = 30) -> dict:
    """
    종목이 최근 days일 내 candle_quality_daily/*.csv 파일들에 등장한 이력 조회.
    필터링 단계에서 가산점 부여 등에 사용.

    Returns:
        {
            'appearances': int,           # 등장 횟수
            'last_seen_date': str | None,
            'dates': list[str],           # 등장한 일자들 (오름차순)
            'history': list[dict],        # [{date, score, hit_count}, ...]
        }
    """
    daily_dir = PROJECT_ROOT / 'candle_quality_daily'
    if not daily_dir.exists():
        return {'appearances': 0, 'last_seen_date': None, 'dates': [], 'history': []}

    today = date.today()
    cutoff = today - timedelta(days=days)
    code_norm = str(code).zfill(6)

    history = []
    for csv_path in sorted(daily_dir.glob('*.csv')):
        try:
            file_date = datetime.strptime(csv_path.stem, '%Y-%m-%d').date()
        except ValueError:
            continue
        if file_date < cutoff or file_date > today:
            continue
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            continue
        if 'code' not in df.columns:
            continue
        df['_code'] = df['code'].astype(str).str.zfill(6)
        row = df[df['_code'] == code_norm]
        if row.empty:
            continue
        r = row.iloc[0]
        history.append({
            'date': csv_path.stem,
            'score': int(r['score']) if pd.notna(r.get('score')) else None,
            'hit_count': int(r['hit_count']) if 'hit_count' in df.columns and pd.notna(r.get('hit_count')) else None,
        })

    if not history:
        return {'appearances': 0, 'last_seen_date': None, 'dates': [], 'history': []}

    return {
        'appearances': len(history),
        'last_seen_date': history[-1]['date'],
        'dates': [h['date'] for h in history],
        'history': history,
    }


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


KOSPI_INDEX_CODE = '001'
KOSDAQ_INDEX_CODE = '101'


def load_market_change(eval_date: str) -> tuple:
    """
    eval_date 당일 KOSPI/KOSDAQ 등락률(%) 반환.
    분봉 parquet의 당일 첫 'open' → 마지막 'close'로 계산.

    Args:
        eval_date: 'YYYY-MM-DD'

    Returns:
        (kospi_chg_pct, kosdaq_chg_pct)

    Raises:
        FileNotFoundError: 해당 월 parquet 없음
        ValueError: 해당 일자 데이터 없음
    """
    kospi_chg = _calc_index_change(KOSPI_INDEX_CODE, eval_date)
    kosdaq_chg = _calc_index_change(KOSDAQ_INDEX_CODE, eval_date)
    return kospi_chg, kosdaq_chg


def _calc_index_change(index_code: str, eval_date: str) -> float:
    """단일 지수의 당일 등락률 계산."""
    d = datetime.strptime(eval_date, '%Y-%m-%d')
    yyyy = d.strftime('%Y')
    yyyymm = d.strftime('%Y%m')

    parquet_path = MARKET_DATA_ROOT / 'bars_1m' / 'index' / index_code / yyyy / f'{yyyymm}.parquet'

    if not parquet_path.exists():
        raise FileNotFoundError(f'시장지수 parquet 없음: {parquet_path}')

    df = pd.read_parquet(parquet_path)
    df['_date'] = pd.to_datetime(df['dt']).dt.strftime('%Y-%m-%d')
    day_df = df[df['_date'] == eval_date].sort_values('dt')

    if len(day_df) == 0:
        raise ValueError(f'{eval_date} 데이터 없음 in {parquet_path}')

    open_price = float(day_df.iloc[0]['open'])
    close_price = float(day_df.iloc[-1]['close'])

    return (close_price - open_price) / open_price * 100
