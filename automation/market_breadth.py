"""시장 폭(breadth) 지표 모듈 — 거시 모니터 확장 (2026-09-15 지시서).

MDC 일봉 패널(breadth_close_panel.parquet, tools/build_breadth_panel.py 생성)과
KOSPI 지수 일봉 OHLC(kospi_daily_ohlc.parquet)에서 표시 전용 지표를 계산한다.

산출:
- A/D Line (전종목 상승-하락 누적) + 당일 변화
- 200일선 위 종목 비율 (%)
- 코스피 200일선 기울기 (20거래일 변화율 %)
- ADX(14) (추세 강도, 방향 무관)
- ATR(14)/종가 ×100 (변동성 %, VKOSPI 대체)

모두 판정이 아니라 '관찰' — 필터로 승격은 Phase 0 검증 통과 후에만.
"""
import os

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(BASE_DIR, '..', 'config', 'data', 'breadth_close_panel.parquet')
KOSPI_OHLC = os.path.join(BASE_DIR, '..', 'config', 'data', 'kospi_daily_ohlc.parquet')


def _load():
    panel = pd.read_parquet(PANEL) if os.path.exists(PANEL) else None
    ohlc = pd.read_parquet(KOSPI_OHLC) if os.path.exists(KOSPI_OHLC) else None
    return panel, ohlc


def ad_line(panel: pd.DataFrame):
    """일별 상승/하락 종목수 → (누적 A/D 라인, 당일 순상강폭, 상승종목수, 하락종목수)."""
    chg = panel.pct_change()
    up = (chg > 0).sum(axis=1)
    dn = (chg < 0).sum(axis=1)
    diff = (up - dn)
    line = diff.cumsum()
    return line, diff, up, dn


def pct_above_ma200(panel: pd.DataFrame) -> pd.Series:
    ma = panel.rolling(200).mean()
    above = (panel > ma)
    valid = panel.notna() & ma.notna()
    return (above.sum(axis=1) / valid.sum(axis=1).replace(0, np.nan) * 100)


def adx_atr(ohlc: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """표준 Wilder ADX(14) + ATR(14). ohlc: open/high/low/close 컬럼."""
    high, low, close = ohlc['high'], ohlc['low'], ohlc['close']
    up = high.diff()
    dn = -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / n, adjust=False).mean()
    return pd.DataFrame({'adx': adx, 'atr': atr})


def kospi_ma200_slope(close: pd.Series, lookback: int = 20) -> pd.Series:
    ma = close.rolling(200).mean()
    return (ma / ma.shift(lookback) - 1) * 100


def snapshot(panel: pd.DataFrame, ohlc: pd.DataFrame, asof: str = None):
    """최신 스냅샷 dict 계산. asof=YYYYMMDD (없으면 패널 최신일)."""
    out = {}
    if panel is not None and len(panel):
        line, diff, up, dn = ad_line(panel)
        pa = pct_above_ma200(panel)
        out['ad_line'] = int(line.iloc[-1])
        out['ad_change_1d'] = int(diff.iloc[-1])
        out['advancers'] = int(up.iloc[-1])
        out['decliners'] = int(dn.iloc[-1])
        p = pa.iloc[-1]
        out['pct_above_ma200'] = round(float(p), 1) if not pd.isna(p) else None
        out['panel_last_date'] = str(panel.index[-1].date())
    if ohlc is not None and len(ohlc) >= 220:
        close = ohlc['close'].astype(float)
        slope = kospi_ma200_slope(close)
        ax = adx_atr(ohlc)
        out['ma200_slope_20d'] = round(float(slope.iloc[-1]), 1)
        out['ma200_slope_20d_prev'] = round(float(slope.iloc[-21]), 1) \
            if len(slope) > 21 and not pd.isna(slope.iloc[-21]) else None
        a = float(ax['adx'].iloc[-1])
        out['adx_14'] = round(a, 1) if not pd.isna(a) else None
        c = float(close.iloc[-1])
        out['atr14_pct'] = round(float(ax['atr'].iloc[-1]) / c * 100, 2) \
            if c > 0 else None
        out['kospi_last_date'] = str(close.index[-1].date())
    return out


def adx_word(adx):
    if adx is None:
        return ''
    if adx < 20:
        return '추세 약함'
    if adx <= 25:
        return '약한 추세'
    return '추세 뚜렷'


def z5_band(z5):
    """z5 절대값 3단계 밴드 — 지시서 추가 작업 (단기 라벨 신뢰도 표시)."""
    if z5 is None:
        return ''
    a = abs(z5)
    if a < 0.5:
        return '보합'
    if a < 0.8:
        return '약한'
    return '명확한'
