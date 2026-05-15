"""
오늘 수집한 종목들의 D 점수 분포 확인 (7일 윈도우, 0~10점).
bimodal / 변별력 부족 / 항목 통과율 편향 체크.

데이터 소스:
- 분봉: \\\\beelink\\market_data\\bars_1m\\stocks\\{code}\\{YYYY}\\{YYYYMM}.parquet (월 단위)
  → 일자별 OHLCV로 groupby 집계해서 사용
- 수집풀: D:\\Kiwoom_RtoB\\config\\data\\collection_pool.json

환경변수로 경로 외부화 가능: MARKET_DATA_ROOT, COLLECTION_POOL_PATH
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sector.candle_quality import evaluate_candle_quality

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARKET_DATA_ROOT = Path(os.environ.get('MARKET_DATA_ROOT', r'\\beelink\market_data'))
COLLECTION_POOL = Path(os.environ.get(
    'COLLECTION_POOL_PATH',
    str(PROJECT_ROOT / 'config' / 'data' / 'collection_pool.json'),
))


def load_7d_bars(code: str, today: date | None = None) -> pd.DataFrame | None:
    """
    종목의 분봉 parquet(월 단위)을 읽어 최근 7거래일 일봉 OHLCV로 집계.
    데이터 부족 시 None.
    """
    if today is None:
        today = date.today()

    # 최근 2개월 분봉이면 거래일 7일 충분히 커버
    cur_month_first = today.replace(day=1)
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

    daily = daily[daily['date'] <= today]
    daily = daily.sort_values('date').reset_index(drop=True)

    if len(daily) < 7:
        return None

    return daily.tail(7).reset_index(drop=True)


def load_today_pool_codes() -> list:
    """수집풀에서 종목 코드 리스트 반환."""
    if not COLLECTION_POOL.exists():
        print(f"[수집풀 없음] {COLLECTION_POOL}")
        return []
    with COLLECTION_POOL.open('r', encoding='utf-8') as f:
        pool = json.load(f)
    if not isinstance(pool, dict):
        return []
    return list(pool.keys())


def test_distribution(stock_codes: list, save_csv: bool = True):
    results = []
    skipped_no_data = 0
    for code in stock_codes:
        bars = load_7d_bars(code)
        if bars is None or len(bars) < 7:
            skipped_no_data += 1
            continue
        r = evaluate_candle_quality(bars)
        row = {
            'code': code,
            'score': r['score'],
            'pullback_pct': r['pullback_depth_pct'],
            'bullish_ratio': r['bullish_ratio'],
            'avg_wick': r['avg_upper_wick'],
        }
        row.update(r['breakdown'])
        results.append(row)

    if not results:
        print(f"\n평가 가능한 종목 없음 (입력 {len(stock_codes)}개, 데이터 부족 {skipped_no_data}개)")
        return None

    df = pd.DataFrame(results)

    print(f"\n입력 {len(stock_codes)}개 | 평가 {len(df)}개 | 데이터 부족 {skipped_no_data}개")

    print("\n=== 전체 점수 분포 (0~10) ===")
    print(df['score'].value_counts().sort_index())
    print(f"\n평균: {df['score'].mean():.2f}, 표준편차: {df['score'].std():.2f}")

    print("\n=== 항목별 평균 점수 ===")
    max_map = {'D1': 2, 'D2': 1, 'D3': 2, 'D4': 1, 'D5': 1, 'D6': 2, 'D7': 1}
    for col in ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7']:
        max_val = max_map[col]
        avg = df[col].mean()
        rate = avg / max_val * 100
        print(f"  {col} (최대 {max_val}): 평균 {avg:.2f} ({rate:.1f}%)")

    print("\n=== 점수 구간별 종목 수 ===")
    bins = [0, 3, 5, 7, 11]
    labels = ['낮음(0-2)', '보통(3-4)', '좋음(5-6)', '우수(7-10)']
    df['grade'] = pd.cut(df['score'], bins=bins, labels=labels, right=False)
    print(df['grade'].value_counts())

    print("\n=== 점수별 샘플 종목 (각 3개) ===")
    for s in sorted(df['score'].unique()):
        sample = df[df['score'] == s].head(3)
        print(f"\n[{s}점] ({len(df[df['score']==s])}개)")
        print(sample[['code', 'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'pullback_pct']].to_string(index=False))

    if save_csv:
        out_path = PROJECT_ROOT / 'candle_quality_distribution.csv'
        df.to_csv(out_path, index=False)
        print(f"\nCSV 저장: {out_path}")
    return df


if __name__ == '__main__':
    codes = load_today_pool_codes()
    print(f"수집풀 종목 수: {len(codes)}")
    if codes:
        test_distribution(codes)
