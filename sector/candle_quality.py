"""
HTS 조건검색 "상승지속형 다른목" 통과 종목의 7일 캔들 품질 평가.
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
        leading_threshold: 주도 양봉 후보 임계값 (%, default 5.0)

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
    # 2~4일 전 (윈도우 중간) 위치가 이상적
    # 당일 = idx 6, 1일전 = idx 5, ... 6일전 = idx 0
    if leading_idx is not None:
        days_ago = 6 - leading_idx  # 당일=0, 6일전=6
        if 2 <= days_ago <= 4:
            breakdown['D2'] = 1
            details['D2_position_ok'] = True
        details['D2_days_ago'] = days_ago

    # ---------- D3. 눌림 깊이 (0~2) ----------
    pullback_pct = 0.0
    if leading is not None:
        leading_high = leading['high']
        # 주도 양봉 이후 ~ 당일 직전 구간 최저가
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
    # 주도일 거래량 급증 + 조정일 감소 + 당일 회복
    if leading is not None and leading_idx < 6:
        leading_vol = leading['volume']
        adj_bars = bars.iloc[leading_idx + 1:-1]
        today_vol = today['volume']

        if len(adj_bars) > 0:
            adj_avg_vol = adj_bars['volume'].mean()

            cond_surge = leading_vol > adj_avg_vol * 1.5
            cond_decline = adj_avg_vol < leading_vol * 0.7
            cond_recover = today_vol > adj_avg_vol * 1.2

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
