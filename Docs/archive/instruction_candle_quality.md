# 지시서: 5일 일봉 캔들 품질 평가 모듈

## 목적
HTS 조건검색 "상승지속후 눌림목" 통과 종목의 최근 5일 일봉 품질을 평가.
점수 항목 D로 사용 (0~4점). 분포 확인 후 가중치/구조 조정 예정.

---

## 1. 모듈 파일

**경로:** `kiwoom_RtoB/sector/candle_quality.py`

```python
"""
HTS 조건검색 통과 종목의 최근 5일 일봉 품질 평가.
점수 항목 D로 사용 (0~4점).
"""
import pandas as pd


def evaluate_5d_candle_quality(
    bars_5d: pd.DataFrame,
    leading_threshold: float = 5.0
) -> dict:
    """
    Args:
        bars_5d: 최근 5거래일 OHLCV (오름차순, 5행)
                 columns: date, open, high, low, close, volume
        leading_threshold: 주도 양봉 판정 임계값 (%, default 5.0)
    
    Returns:
        {
            'score': int (0~4),
            'leading_bar': dict | None,
            'bullish_ratio': float,
            'avg_upper_wick': float,
            'details': dict (4개 항목 bool)
        }
    """
    details = {
        'leading_short_wick': False,
        'leading_long_body': False,
        'bullish_majority': False,
        'low_avg_wick': False,
    }
    
    if bars_5d is None or len(bars_5d) < 5:
        return {'score': 0, 'leading_bar': None, 'bullish_ratio': 0,
                'avg_upper_wick': 0, 'details': details}
    
    bullish_days = []
    for _, day in bars_5d.iterrows():
        o, h, l, c = day['open'], day['high'], day['low'], day['close']
        rng = h - l + 0.001
        if c > o:
            bullish_days.append({
                'date': day['date'],
                'body_ratio': abs(c - o) / rng,
                'upper_wick': (h - c) / rng,
                'change_pct': (c - o) / o * 100,
                'ohlc': (o, h, l, c),
            })
    
    if not bullish_days:
        return {'score': 0, 'leading_bar': None, 'bullish_ratio': 0,
                'avg_upper_wick': 0, 'details': details}
    
    score = 0
    
    # 1, 2. 주도 양봉 (5% 이상 상승한 양봉 중 최대)
    leading = max(bullish_days, key=lambda x: x['change_pct'])
    if leading['change_pct'] >= leading_threshold:
        if leading['upper_wick'] < 0.20:
            score += 1
            details['leading_short_wick'] = True
        if leading['body_ratio'] > 0.60:
            score += 1
            details['leading_long_body'] = True
    
    # 3. 양봉 우세 (5일 중 3일 이상)
    bullish_ratio = len(bullish_days) / 5
    if len(bullish_days) >= 3:
        score += 1
        details['bullish_majority'] = True
    
    # 4. 평균 윗꼬리 < 25%
    avg_upper = sum(d['upper_wick'] for d in bullish_days) / len(bullish_days)
    if avg_upper < 0.25:
        score += 1
        details['low_avg_wick'] = True
    
    return {
        'score': score,
        'leading_bar': leading,
        'bullish_ratio': bullish_ratio,
        'avg_upper_wick': avg_upper,
        'details': details,
    }
```

---

## 2. 검증 스크립트

**경로:** `kiwoom_RtoB/tools/test_candle_quality_distribution.py`

```python
"""
오늘 수집풀 종목들의 점수 분포 확인.
bimodal 분포면 임계값 조정 필요.
"""
import pandas as pd
from sector.candle_quality import evaluate_5d_candle_quality
# bar_storage / 수집풀 로더는 kiwoom_RtoB 기존 모듈에 맞춰 import


def test_distribution(stock_codes: list, save_csv: bool = True):
    results = []
    for code in stock_codes:
        bars = load_5d_bars(code)  # bar_storage 인터페이스에 맞춰 구현
        if bars is None or len(bars) < 5:
            continue
        r = evaluate_5d_candle_quality(bars)
        results.append({
            'code': code,
            'score': r['score'],
            'bullish_ratio': r['bullish_ratio'],
            'avg_wick': r['avg_upper_wick'],
            **r['details'],
        })
    
    df = pd.DataFrame(results)
    
    print("\n=== 점수 분포 ===")
    print(df['score'].value_counts().sort_index())
    
    print("\n=== 항목별 통과율 ===")
    for col in ['leading_short_wick', 'leading_long_body',
                'bullish_majority', 'low_avg_wick']:
        print(f"  {col}: {df[col].sum() / len(df) * 100:.1f}%")
    
    print("\n=== 점수별 샘플 ===")
    for s in range(5):
        sample = df[df['score'] == s].head(3)
        if not sample.empty:
            print(f"\n[{s}점]")
            print(sample[['code', 'bullish_ratio', 'avg_wick']])
    
    if save_csv:
        df.to_csv('candle_quality_distribution.csv', index=False)
    return df


if __name__ == '__main__':
    codes = load_today_pool_codes()  # 수집풀 로더 연결
    test_distribution(codes)
```

---

## 3. 작업 순서
1. `candle_quality.py` 생성
2. `test_candle_quality_distribution.py` 작성 — bar_storage 로더, 수집풀 코드 로더는 kiwoom_RtoB 기존 구조에 맞춰 연결
3. 오늘 수집풀 종목으로 실행 → `candle_quality_distribution.csv` 생성
4. 분포 결과 보고

---

## 4. 보고 양식
```
점수 분포:
  0점: N개 (X%)
  1점: ...
  4점: ...

항목별 통과율:
  leading_short_wick: X%
  leading_long_body: X%
  bullish_majority: X%
  low_avg_wick: X%

bimodal 여부: O/X
```

---

## 5. 주의
- 점수 체계 통합/가중치 조정은 분포 확인 후 별도 지시
- score.py 통합 작업은 이번 범위 밖
- git commit → push → SSH Beelink → pull (4단계 필수)
