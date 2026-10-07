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


def build(years=None, out_panel=None):
    years = tuple(years) if years else YEARS
    out_panel = out_panel or OUT_PANEL
    t0 = time.time()
    folders = sorted(glob.glob(os.path.join(STOCKS_DIR, '*')))
    print(f'종목 폴더: {len(folders)} | 연도: {years} | 출력: {out_panel}')
    cols = ['dt', 'close']
    series = {}
    for i, folder in enumerate(folders):
        code = os.path.basename(folder)
        frames = []
        for y in years:
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
    panel.to_parquet(out_panel)
    print(f'패널 저장: {panel.shape} → {out_panel} ({time.time()-t0:.0f}초)')

    # KOSPI 지수 OHLC 저장 제거 (2026-10-07) — kospi_daily_ohlc.parquet의 단일 소유자는
    # ka20006(운영 macro_monitor.run_daily가 매일 갱신). MDC 지수와 소스 경쟁하던 문제 정리.
    if out_panel != OUT_PANEL:
        return
    print(f'KOSPI OHLC는 ka20006 소스가 소유 — 여기서 저장하지 않음')


if __name__ == '__main__':
    # 사용법: python build_breadth_panel.py [연도,쉼표구분] [출력파일]
    ys = sys.argv[1].split(',') if len(sys.argv) > 1 else None
    op = sys.argv[2] if len(sys.argv) > 2 else None
    build(ys, op)
