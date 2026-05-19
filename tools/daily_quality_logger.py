"""
매일 장 마감 후 실행.
1. 오늘 수집한 종목들의 D 점수 평가
2. 시장 상태 기록
3. 이전 평가한 종목들의 익일 수익률 사후 기록
4. 마스터 CSV에 누적
"""
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import sys
import os

logger = logging.getLogger(__name__)

# 상위 디렉토리 추가하여 sector 모듈 임포트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sector.candle_quality import evaluate_candle_quality
from tools.data_loaders import (
    load_today_pool_codes,
    load_today_pool_full,
    load_7d_bars,
    lookup_close,
    load_market_change,
)
# automation/utils 패키지 경로 추가 (sys.path 위에서 PROJECT_ROOT 추가됨)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'automation'))
from utils.collection_pool import clear_pool


DAILY_DIR = Path('candle_quality_daily')
MASTER_CSV = Path('candle_quality_master.csv')


def evaluate_today_pool(eval_date: str) -> pd.DataFrame:
    """오늘 수집한 종목 평가 + 시장 상태 기록."""
    pool = load_today_pool_full()
    codes = list(pool.keys())

    # 빈 풀이면 CSV 생성 스킵 — 빈 컬럼 헤더 없는 CSV는 read_csv에서 EmptyDataError
    if not codes:
        logger.info(f"[일일 평가] {eval_date}: 풀 0건 — CSV 생성 스킵")
        return pd.DataFrame()

    kospi_chg, kosdaq_chg = load_market_change(eval_date)

    results = []
    for code in codes:
        bars = load_7d_bars(code, end_date=eval_date)
        if bars is None or len(bars) < 7:
            continue
        r = evaluate_candle_quality(bars)
        today_close = bars.iloc[-1]['close']
        pool_entry = pool.get(code, {})

        row = {
            'eval_date': eval_date,
            'code': code,
            'today_close': today_close,
            'score': r['score'],
            'pullback_pct': r['pullback_depth_pct'],
            'bullish_ratio': r['bullish_ratio'],
            'avg_wick': r['avg_upper_wick'],
            'hit_count': int(pool_entry.get('hit_count', 0)),  # 풀에서 그날 매칭 횟수
            'first_seen': pool_entry.get('first_seen'),
            'last_seen': pool_entry.get('last_seen'),
            'seq_ids': ','.join(pool_entry.get('seq_ids', [])),
            'kospi_chg': kospi_chg,
            'kosdaq_chg': kosdaq_chg,
            'eval_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            # 사후 검증용 (다음 거래일에 채워짐)
            'd1_close': None,
            'd1_return_pct': None,
            'd5_close': None,
            'd5_return_pct': None,
        }
        row.update(r['breakdown'])
        results.append(row)

    if not results:
        # 빈 풀(평가 가능 종목 0건)이면 CSV 만들지 않음 — 빈 컬럼 헤더 없는 CSV는 후속 read에서 EmptyDataError 유발
        print(f"[일일 평가] {eval_date}: 0건 (collection_pool 비어있음, CSV 생성 skip)")
        return pd.DataFrame()
    if not results:
        logger.info(f"[일일 평가] {eval_date}: 평가 가능 종목 0건 — CSV 생성 스킵")
        return pd.DataFrame()

    df = pd.DataFrame(results)
    daily_path = DAILY_DIR / f'{eval_date}.csv'
    df.to_csv(daily_path, index=False)
    logger.info(f"[일일 평가] {eval_date}: {len(df)}건 저장 → {daily_path}")
    return df


def backfill_returns(eval_date: str):
    """
    eval_date의 1거래일 후 / 5거래일 후 수익률을 사후 기록.
    매일 실행 시 과거 7일치 데이터의 미완성 컬럼을 채움.
    """
    cutoff = datetime.strptime(eval_date, '%Y-%m-%d') - timedelta(days=10)

    for daily_file in DAILY_DIR.glob('*.csv'):
        file_date = datetime.strptime(daily_file.stem, '%Y-%m-%d')
        if file_date < cutoff:
            continue

        # code는 6자리 zero-padded 종목코드 — int 추론 방지 (앞 0이 잘리면 lookup_close 실패)
        try:
            df = pd.read_csv(daily_file, dtype={'code': str})
        except pd.errors.EmptyDataError:
            logger.warning(f"empty CSV skipped in backfill: {daily_file.name}")
            continue
        updated = False

        for idx, row in df.iterrows():
            if pd.isna(row['d1_return_pct']):
                d1_close = lookup_close(row['code'], row['eval_date'], offset_bdays=1)
                if d1_close is not None:
                    df.at[idx, 'd1_close'] = d1_close
                    df.at[idx, 'd1_return_pct'] = (d1_close - row['today_close']) / row['today_close'] * 100
                    updated = True

            if pd.isna(row['d5_return_pct']):
                d5_close = lookup_close(row['code'], row['eval_date'], offset_bdays=5)
                if d5_close is not None:
                    df.at[idx, 'd5_close'] = d5_close
                    df.at[idx, 'd5_return_pct'] = (d5_close - row['today_close']) / row['today_close'] * 100
                    updated = True

        if updated:
            df.to_csv(daily_file, index=False)
            print(f"[사후 기록] {daily_file.stem}: 수익률 보충")


def rebuild_master():
    """일일 CSV들을 마스터 CSV로 통합."""
    dfs = []
    for daily_file in sorted(DAILY_DIR.glob('*.csv')):
        try:
            df = pd.read_csv(daily_file, dtype={'code': str})
            dfs.append(df)
        except pd.errors.EmptyDataError:
            logger.warning(f"empty CSV skipped in rebuild: {daily_file.name}")
            continue
    if dfs:
        master = pd.concat(dfs, ignore_index=True)
        master.to_csv(MASTER_CSV, index=False)
        logger.info(f"[마스터] {len(master)}건 → {MASTER_CSV}")
        return master
    return pd.DataFrame()


if __name__ == '__main__':
    # DEPRECATED — 2026-05-19
    # 외부 프로세스로 직접 실행 금지.
    # daily 작업은 봇 내부 task(automation/core/daily_task.py)에서만 호출.
    # 영구 원칙(메모리): 외부 프로세스에서 봇 데이터 파일 직접 조작 금지.
    # 5/18, 5/19 두 차례 race condition으로 collection_pool 비우기 실패한 사고 재발 방지.
    raise RuntimeError(
        "daily_quality_logger는 봇 내부에서만 호출됩니다. "
        "직접 실행 금지 (영구 원칙: 외부 프로세스 데이터 조작 금지). "
        "봇이 매일 16:30에 자체적으로 실행함."
    )
