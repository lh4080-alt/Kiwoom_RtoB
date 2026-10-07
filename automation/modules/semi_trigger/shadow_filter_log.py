# -*- coding: utf-8 -*-
"""눌림매수 필터 섀도 로깅 (2026-10-06 Lee 지시 — 3protv 동결과 별개, 표시 전용 축적).

semi_trigger 파이프라인 실행 시점의 필터 입력 지표를 jsonl로 기록만 한다
(매수·알림 동작 없음). 로그가 쌓이면 IC 도구와 같은 규칙으로 필터 검증.

지표 (MDC bars_1d 종목 일봉 + kospi_daily_ohlc.parquet — 전일 값, 룩어헤드 없음):
  atr_pctile: ATR% 252일 롤링 백분위 (전일 — 당일 결정에 사용 가능한 마지막 값)
  adx/di_plus/di_minus: Wilder ADX(14)
  disp60: MA60 이격도(%)  |  dd250: 250일 고점대비(%)  |  kospi_z5: 코스피 5일 수익률 z
저장: config/data/semi_shadow.jsonl — 같은 (date, code) 재실행 시 중복 스킵.
"""
import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BARS_DIR = r'C:\market_data\bars_1d\stocks'
KOSPI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', '..', '..', 'config', 'data', 'kospi_daily_ohlc.parquet')
SHADOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', '..', '..', 'config', 'data', 'semi_shadow.jsonl')
WIN = 252


def _load_stock(code: str) -> pd.DataFrame:
    import glob
    frames = [pd.read_parquet(f) for f in
              sorted(glob.glob(os.path.join(BARS_DIR, code, '*.parquet')))]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df['dt'] = pd.to_datetime(df['dt'])
    return df.drop_duplicates('dt').set_index('dt').sort_index()


def _stock_indicators(df: pd.DataFrame) -> dict:
    """전일(t-1) 시점 값들 — 당일 결정에 실제로 쓸 수 있는 정보만."""
    if df.empty or len(df) < 260:
        return {}
    close, high, low = (df[k].astype(float) for k in ('close', 'high', 'low'))
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    atr_pct = atr / close * 100
    atr_pctile = (atr_pct.rolling(WIN, min_periods=120).rank(pct=True) * 100)
    # Wilder ADX/DI
    up, dn = high.diff(), -low.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr.replace(0, np.nan)
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()
    disp60 = (close / close.rolling(60).mean() - 1) * 100
    dd250 = (close / close.rolling(250).max() - 1) * 100
    i = -2  # 전일 (마지막 행은 당일 — semi morning은 장 전 실행)
    def _r(v):
        return round(float(v), 2) if not pd.isna(v) else None
    return {'atr_pctile': _r(atr_pctile.iloc[i]), 'adx': _r(adx.iloc[i]),
            'di_plus': _r(pdi.iloc[i]), 'di_minus': _r(mdi.iloc[i]),
            'disp60': _r(disp60.iloc[i]), 'dd250': _r(dd250.iloc[i])}


def _kospi_z5() -> float | None:
    try:
        ohlc = pd.read_parquet(KOSPI_PATH)
        c = ohlc['close'].astype(float)
        r5 = c.pct_change(5) * 100
        z = (r5 - r5.rolling(60).mean()) / r5.rolling(60).std()
        v = z.iloc[-2]  # 전일
        return round(float(v), 2) if not pd.isna(v) else None
    except Exception:
        return None


def _new_signal_z(path: str, cur_mu, cur_sox) -> dict:
    """§7 신규 신호 섀도 — MU 단독 z + SOX z + MU-SOX 스프레드 z (임시: 섀도 이력 60일 기준).

    조건 (설정: 지시서 §7, 임계값은 config 분리 예정): MU z<=-1.5 AND SOX z<=-0.5.
    z는 섀도 이력 30행 이상부터 제공 (그 전엔 None — 신호 판정 없이 값만 축적).
    """
    mu_hist, sox_hist = [], []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('mu') is not None and r.get('sox') is not None:
                    mu_hist.append(r['mu'])
                    sox_hist.append(r['sox'])

    def _z(arr, cur):
        if cur is None or len(arr) < 30:
            return None
        a = np.array(arr[-60:], dtype=float)
        sd = a.std()
        return round(float((cur - a.mean()) / sd), 2) if sd else None

    spread = (cur_mu - cur_sox) if (cur_mu is not None and cur_sox is not None) else None
    out = {'mu': cur_mu, 'sox': cur_sox, 'mu_sox_spread': round(spread, 2) if spread is not None else None,
           'mu_z': _z(mu_hist, cur_mu), 'sox_z': _z(sox_hist, cur_sox),
           'spread_z': _z(mu_hist and [m - s for m, s in zip(mu_hist, sox_hist)], spread)}
    mz, sz = out['mu_z'], out['sox_z']
    out['new_signal'] = 1 if (mz is not None and sz is not None and mz <= -1.5 and sz <= -0.5) else 0
    return out


def log_shadow(eval_date: str, codes: list, path: str = None,
               us_extra: dict = None) -> int:
    """섀도 레코드 기록. 반환: 기록 행수. 예외는 호출부에서 잡아 무영향 유지."""
    path = path or SHADOW_PATH
    seen = set()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                    seen.add((r['date'], r['code']))
                except Exception:
                    continue
    koz5 = _kospi_z5()
    ns = _new_signal_z(path, (us_extra or {}).get('mu'), (us_extra or {}).get('sox'))
    n = 0
    with open(path, 'a', encoding='utf-8') as f:
        for code in codes:
            if (eval_date, code) in seen:
                continue
            rec = {'date': eval_date, 'code': code,
                   'kospi_z5': koz5,
                   'logged_at': datetime.now().isoformat(timespec='seconds')}
            rec.update(_stock_indicators(_load_stock(code)))
            rec.update(ns)  # §7 신규 신호 섀도 — 로그 전용, 기존 신호 무변경
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            n += 1
    return n
