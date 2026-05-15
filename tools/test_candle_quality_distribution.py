"""
오늘 수집한 종목들의 D 점수 분포 확인 (7일 윈도우, 0~10점).
bimodal / 변별력 부족 / 항목 통과율 편향 체크.

데이터 소스 / 로더: tools/data_loaders.py
"""
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sector.candle_quality import evaluate_candle_quality
from tools.data_loaders import load_today_pool_codes, load_7d_bars, PROJECT_ROOT


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
