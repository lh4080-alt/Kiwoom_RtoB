# -*- coding: utf-8 -*-
"""MDC 일봉 → breadth 패널 빌더 (1회성 배치 + 증분).

C:\market_data\bars_1d\stocks\{code}\{YYYY}.parquet (dt,code,open,high,low,close,volume,value)
에서 종가 패널(날짜×종목)을 만들어
C:\Kiwoom_RtoB\config\data\breadth_close_panel.parquet 로 저장.
KOSPI 지수 일봉 OHLC는 ...index\001 에서 kospi_daily_ohlc.parquet 로 저장.

용도: 거시 모니터 확장 지표 (A/D Line, 200일선 위 비율) — 2026-09-15 지시서.
"""
import glob
import os
import sys
import time

import pandas as pd

STOCKS_DIR = r'C:\market_data\bars_1d\stocks'
INDEX_DIR = r'C:\market_data\bars_1d\index\001'
OUT_PANEL = r'C:\Kiwoom_RtoB\config\data\breadth_close_panel.parquet'
OUT_KOSPI = r'C:\Kiwoom_RtoB\config\data\kospi_daily_ohlc.parquet'
YEARS = ('2025', '2026')  # MA200(200거래일≈10개월) + 여유 → 최근 2개 연도면 충분


def build():
    t0 = time.time()
    folders = sorted(glob.glob(os.path.join(STOCKS_DIR, '*')))
    print(f'종목 폴더: {len(folders)}')
    cols = ['dt', 'close']
    series = {}
    for i, folder in enumerate(folders):
        code = os.path.basename(folder)
        frames = []
        for y in YEARS:
            fs = glob.glob(os.path.join(folder, y + '.parquet'))
            if fs:
                try:
                    df = pd.read_parquet(fs[0], columns=cols)
                    frames.append(df)
                except Exception as e:
                    print(f'  {code} {y} 읽기 실패: {e}')
        if not frames:
            continue
        df = pd.concat(frames)
        df['dt'] = pd.to_datetime(df['dt'])
        s = df.drop_duplicates('dt').set_index('dt')['close'].astype(float).sort_index()
        if len(s):
            series[code] = s
        if (i + 1) % 500 == 0:
            print(f'  {i+1}/{len(folders)} 처리 ({time.time()-t0:.0f}초)')
    panel = pd.DataFrame(series)
    panel.index = pd.to_datetime(panel.index)
    panel = panel.sort_index()
    panel.to_parquet(OUT_PANEL)
    print(f'패널 저장: {panel.shape} → {OUT_PANEL} ({time.time()-t0:.0f}초)')

    # KOSPI 지수 OHLC
    fs = []
    for y in YEARS + ('2024',):
        fs.extend(glob.glob(os.path.join(INDEX_DIR, y + '.parquet')))
    if fs:
        df = pd.concat([pd.read_parquet(f) for f in fs])
        df['dt'] = pd.to_datetime(df['dt'])
        df = df.drop_duplicates('dt').set_index('dt').sort_index()
        keep = [c for c in ('open', 'high', 'low', 'close', 'volume') if c in df.columns]
        df[keep].to_parquet(OUT_KOSPI)
        print(f'지수 OHLC 저장: {len(df)}행 → {OUT_KOSPI}')


if __name__ == '__main__':
    build()
