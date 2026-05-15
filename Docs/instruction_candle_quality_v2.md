# 지시서: 7일 일봉 캔들 품질 평가 모듈 (D 점수, 0~10점)

## 목적
HTS 조건검색 "상승지속후 눌림목" (7봉 기준) 통과 종목의 캔들 품질 평가.
점수 항목 D로 사용 (0~10점). 분포 검증 후 가중치 조정 예정.

## 전제
- HTS 조건검색 A 항목 이미 5봉 → 7봉으로 수정 완료
- 입력: 최근 7거래일 일봉 OHLCV + 당일 캔들 (장중/장마감)

---

## 1. 모듈 파일

**경로:** `kiwoom_RtoB/sector/candle_quality.py`

```python
"""
HTS 조건검색 "상승지속후 눌림목" 통과 종목의 7일 캔들 품질 평가.
점수 항목 D로 사용 (0~10점).

D1. 주도 양봉 강도 (0~2)
D2. 주도 양봉 위치 (0~1)
D3. 눌림 깊이 (0~2)
D4. 양봉 우세 (0~1)
D5. 평균 윗꼬리 (0~1)
D6. 당일 캔들 (0~2)
D7. 거래량 패턴 (0~1)
"""
import pandas as pd


def evaluate_candle_quality(
    bars_7d: pd.DataFrame,
    leading_threshold: float = 5.0
) -> dict:
    """
    Args:
        bars_7d: 최근 7거래일 OHLCV (오름차순, 7행)
                 마지막 행 = 당일 캔들
                 columns: date, open, high, low, close, volume
        leading_threshold: 주도 양봉 판정 임계값 (%, default 5.0)
    
    Returns:
        {
            'score': int (0~10),
            'breakdown': {'D1':int, 'D2':int, ..., 'D7':int},
            'leading_bar': dict | None,
            'leading_idx': int | None,  # 0~6, 윈도우 내 위치
            'pullback_depth_pct': float,
            'today_candle': dict,
            'details': dict
        }
    """
    breakdown = {f'D{i}': 0 for i in range(1, 8)}
    details = {}
    
    if bars_7d is None or len(bars_7d) < 7:
        return _empty_result(breakdown, details)
    
    bars = bars_7d.reset_index(drop=True)
    today = bars.iloc[-1]
    
    # 양봉 리스트 구성
    bullish_days = []
    for idx, day in bars.iterrows():
        o, h, l, c = day['open'], day['high'], day['low'], day['close']
        rng = h - l + 0.001
        if c > o:
            bullish_days.append({
                'idx': idx,
                'date': day['date'],
                'body_ratio': abs(c - o) / rng,
                'upper_wick': (h - c) / rng,
                'change_pct': (c - o) / o * 100,
                'high': h,
                'volume': day['volume'],
                'ohlc': (o, h, l, c),
            })
    
    # ---------- D1. 주도 양봉 강도 (0~2) ----------
    leading = None
    leading_idx = None
    if bullish_days:
        leading = max(bullish_days, key=lambda x: x['change_pct'])
        leading_idx = leading['idx']
        
        if leading['change_pct'] >= leading_threshold:
            if leading['upper_wick'] < 0.20:
                breakdown['D1'] += 1
                details['D1_short_wick'] = True
            if leading['body_ratio'] > 0.60:
                breakdown['D1'] += 1
                details['D1_long_body'] = True
    
    # ---------- D2. 주도 양봉 위치 (0~1) ----------
    # 3~5일 전 (idx 1, 2, 3) 위치가 이상적
    # 당일 = idx 6, 1일전 = idx 5, ... 6일전 = idx 0
    if leading_idx is not None:
        days_ago = 6 - leading_idx  # 당일=0, 6일전=6
        if 2 <= days_ago <= 4:  # 2~4일 전 (윈도우 중간)
            breakdown['D2'] = 1
            details['D2_position_ok'] = True
        details['D2_days_ago'] = days_ago
    
    # ---------- D3. 눌림 깊이 (0~2) ----------
    pullback_pct = 0.0
    if leading is not None:
        leading_high = leading['high']
        # 주도 양봉 이후 최저가 (당일 제외)
        after_leading = bars.iloc[leading_idx + 1:-1]
        if len(after_leading) > 0:
            min_low = after_leading['low'].min()
            pullback_pct = (leading_high - min_low) / leading_high * 100
            
            if 3.0 <= pullback_pct <= 8.0:
                breakdown['D3'] = 2
                details['D3_quality'] = 'healthy'
            elif 0 <= pullback_pct < 3.0 or 8.0 < pullback_pct <= 12.0:
                breakdown['D3'] = 1
                details['D3_quality'] = 'marginal'
            else:
                details['D3_quality'] = 'failed'
    
    # ---------- D4. 양봉 우세 (0~1) ----------
    bullish_ratio = len(bullish_days) / 7
    if len(bullish_days) >= 4:
        breakdown['D4'] = 1
        details['D4_bullish_majority'] = True
    
    # ---------- D5. 평균 윗꼬리 (0~1) ----------
    avg_upper = 0.0
    if bullish_days:
        avg_upper = sum(d['upper_wick'] for d in bullish_days) / len(bullish_days)
        if avg_upper < 0.25:
            breakdown['D5'] = 1
            details['D5_low_avg_wick'] = True
    
    # ---------- D6. 당일 캔들 (0~2) ----------
    o, h, l, c = today['open'], today['high'], today['low'], today['close']
    today_rng = h - l + 0.001
    today_upper = (h - c) / today_rng
    today_close_pos = (c - l) / today_rng
    is_today_bullish = c > o
    
    if is_today_bullish and today_close_pos >= 0.70:
        breakdown['D6'] += 1
        details['D6_strong_close'] = True
    if today_upper < 0.20:
        breakdown['D6'] += 1
        details['D6_short_wick'] = True
    
    today_candle = {
        'bullish': is_today_bullish,
        'upper_wick': today_upper,
        'close_position': today_close_pos,
        'ohlc': (o, h, l, c),
    }
    
    # ---------- D7. 거래량 패턴 (0~1) ----------
    # 주도일 거래량 폭증 + 조정일 감소 + 당일 회복
    if leading is not None and leading_idx < 6:
        leading_vol = leading['volume']
        # 조정 구간 평균 거래량 (주도 양봉 다음 ~ 당일 직전)
        adj_bars = bars.iloc[leading_idx + 1:-1]
        today_vol = today['volume']
        
        if len(adj_bars) > 0:
            adj_avg_vol = adj_bars['volume'].mean()
            
            cond_surge = leading_vol > adj_avg_vol * 1.5  # 주도일 거래량 폭증
            cond_decline = adj_avg_vol < leading_vol * 0.7  # 조정일 감소
            cond_recover = today_vol > adj_avg_vol * 1.2  # 당일 회복
            
            if cond_surge and cond_decline and cond_recover:
                breakdown['D7'] = 1
                details['D7_volume_pattern_ok'] = True
            
            details['D7_leading_vol'] = int(leading_vol)
            details['D7_adj_avg_vol'] = int(adj_avg_vol)
            details['D7_today_vol'] = int(today_vol)
    
    score = sum(breakdown.values())
    
    return {
        'score': score,
        'breakdown': breakdown,
        'leading_bar': leading,
        'leading_idx': leading_idx,
        'pullback_depth_pct': pullback_pct,
        'bullish_ratio': bullish_ratio,
        'avg_upper_wick': avg_upper,
        'today_candle': today_candle,
        'details': details,
    }


def _empty_result(breakdown, details):
    return {
        'score': 0,
        'breakdown': breakdown,
        'leading_bar': None,
        'leading_idx': None,
        'pullback_depth_pct': 0.0,
        'bullish_ratio': 0.0,
        'avg_upper_wick': 0.0,
        'today_candle': None,
        'details': details,
    }
```

---

## 2. 검증 스크립트

**경로:** `kiwoom_RtoB/tools/test_candle_quality_distribution.py`

```python
"""
오늘 수집풀 종목들의 D 점수 분포 확인.
bimodal / 변별력 부족 / 항목 통과율 편향 체크.
"""
import pandas as pd
from sector.candle_quality import evaluate_candle_quality
# bar_storage / 수집풀 로더는 kiwoom_RtoB 기존 모듈에 맞춰 import


def test_distribution(stock_codes: list, save_csv: bool = True):
    results = []
    for code in stock_codes:
        bars = load_7d_bars(code)  # bar_storage 인터페이스에 맞춰 구현
        if bars is None or len(bars) < 7:
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
    
    df = pd.DataFrame(results)
    
    print("\n=== 전체 점수 분포 (0~10) ===")
    print(df['score'].value_counts().sort_index())
    print(f"\n평균: {df['score'].mean():.2f}, 표준편차: {df['score'].std():.2f}")
    
    print("\n=== 항목별 평균 점수 ===")
    for col in ['D1','D2','D3','D4','D5','D6','D7']:
        max_val = {'D1':2,'D2':1,'D3':2,'D4':1,'D5':1,'D6':2,'D7':1}[col]
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
        print(sample[['code','D1','D2','D3','D4','D5','D6','D7','pullback_pct']].to_string(index=False))
    
    if save_csv:
        df.to_csv('candle_quality_distribution.csv', index=False)
    return df


if __name__ == '__main__':
    codes = load_today_pool_codes()  # 수집풀 로더 연결
    test_distribution(codes)
```

---

## 3. 작업 순서

1. `candle_quality.py` 생성 (위 코드 그대로)
2. `test_candle_quality_distribution.py` 작성
   - `load_7d_bars(code)` — bar_storage 기존 인터페이스 연결
   - `load_today_pool_codes()` — waiting_pool 또는 수집풀 결과 파일 연결
3. 오늘 수집풀 종목으로 실행 → `candle_quality_distribution.csv` 생성
4. 결과 보고

---

## 4. 보고 양식

```
전체 점수 분포:
  0점: N개
  1점: ...
  ...
  10점: ...
  평균: X.XX
  표준편차: X.XX

항목별 평균 점수 (괄호=통과율):
  D1 (최대 2): X.XX (XX%)
  D2 (최대 1): X.XX (XX%)
  D3 (최대 2): X.XX (XX%)
  D4 (최대 1): X.XX (XX%)
  D5 (최대 1): X.XX (XX%)
  D6 (최대 2): X.XX (XX%)
  D7 (최대 1): X.XX (XX%)

구간별:
  낮음(0-2): N개
  보통(3-4): N개
  좋음(5-6): N개
  우수(7-10): N개

이슈:
  - bimodal 여부
  - 변별력 부족 항목 (통과율 95%+ 또는 5% 이하)
  - 0/10점 극단치
```

---

## 5. 주의

- score.py 통합 / 가중치 조정은 분포 확인 후 별도 지시
- 당일 캔들(D6) 평가는 장중과 종가가 다르므로 호출 시점 명시 필요
  → 일단 호출 시점 그대로 평가, 나중에 시점별 보정 검토
- git commit → push → SSH Beelink → pull (4단계 필수)
