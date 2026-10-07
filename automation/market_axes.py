# -*- coding: utf-8 -*-
"""4축 상태 계산 + 전방 검증 로그 — 최종작업지시서 §3·§4-1 (2026-10-07).

사전 선언 규칙 (state_combo_backtest.py와 동일 — 결과 후 변경 금지):
  축1 강도:   ADX14 >= 20 → 강 / 약
  축2 방향:   close>MA200 & MA20>MA60 → 상승 / close<MA200 & MA20<MA60 → 하락 / else 혼조
  축3 수급:   가격5일>0 & 반도체(삼전·하닉 평균)외인20일누적<0 → 괴리有
              / 가격5일<=0 & 누적>=0 → 無 / else 혼합
  축4 주도주: 삼전 20일 수익 − 코스피 20일 수익 > 0 → 주도유지 / else 주도이탈

record_forward(): 전방 검증 로그 (진짜 홀드아웃) — 매일 1행, 사전 선언 규칙 그대로.
표시 구조와 별개. 6~12개월 축적 후 4-1·신호의 전방 성과를 같은 규칙으로 채점.
"""
import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
STOCKS_DIR = r'C:\market_data\bars_1d\stocks'
STOCK_FLOWS = os.path.join(BASE, '..', 'config', 'data', 'stock_flows.parquet')
FORWARD_PATH = os.path.join(BASE, '..', 'config', 'data', 'forward_log.jsonl')


def compute_axes(today: str, kospi_closes: dict, adx: float) -> dict:
    """4축 라벨 + 200일선 위/아래·이탈 경과일. 결측 축은 '데이터부족'."""
    out = {'axis1': '강' if (adx or 0) >= 20 else '약'}
    ks = sorted(kospi_closes.keys())
    if len(ks) < 260:
        out.update({'axis2': '데이터부족', 'above200': None, 'ma200_break_days': None,
                    'axis3': '데이터부족', 'axis4': '데이터부족'})
        return out
    s = pd.Series({pd.Timestamp(k): v for k, v in kospi_closes.items()}).sort_index()
    c = s.iloc[-1]
    ma200, ma20, ma60 = s.rolling(200).mean(), s.rolling(20).mean(), s.rolling(60).mean()
    above = bool(c > ma200.iloc[-1])
    brk = 0
    if not above:
        for i in range(len(s) - 1, 199, -1):
            if s.iloc[i] > ma200.iloc[i]:
                brk = len(s) - 1 - i
                break
        else:
            brk = len(s) - 200
    out['above200'] = above
    out['ma200_break_days'] = brk if not above else 0
    up_d = (c > ma200.iloc[-1]) and (ma20.iloc[-1] > ma60.iloc[-1])
    dn_d = (c < ma200.iloc[-1]) and (ma20.iloc[-1] < ma60.iloc[-1])
    out['axis2'] = '상승' if up_d else ('하락' if dn_d else '혼조')

    # 축3·4 — ka10059 백필 + MDC 삼전 종가
    try:
        flows = pd.read_parquet(STOCK_FLOWS)
        flows.index = pd.to_datetime(flows.index, format='%Y%m%d')
        last_dt = flows.index.max()
        semi_frgn = (flows['005930_frgnr'] + flows['000660_frgnr']) / 2
        cum20 = semi_frgn.rolling(20).sum().iloc[-1]
        s_k = s.reindex(semi_frgn.index).dropna()
        px5 = (s_k.iloc[-1] / s_k.iloc[-6] - 1) * 100 if len(s_k) >= 6 else None
        frames = [pd.read_parquet(f) for f in
                  sorted(glob.glob(os.path.join(STOCKS_DIR, '005930', '*.parquet')))]
        sd = pd.concat(frames)
        sd['dt'] = pd.to_datetime(sd['dt'])
        sc = sd.drop_duplicates('dt').set_index('dt')['close'].astype(float).sort_index()
        rel = (sc.iloc[-1] / sc.iloc[-21] - 1) * 100 - (c / s.iloc[-21] - 1) * 100
        out['axis3'] = ('괴리有' if (px5 is not None and px5 > 0 and cum20 < 0)
                        else ('無' if (px5 is not None and px5 <= 0 and cum20 >= 0) else '혼합'))
        out['axis4'] = '주도유지' if rel > 0 else '주도이탈'
        out['semi_frgn_cum20_eok'] = round(float(cum20), 1)
    except Exception:
        out['axis3'] = out['axis4'] = '데이터부족'
    return out


def record_forward(today: str, axes: dict, breadth: dict) -> dict:
    """전방 검증 로그 1행 기록 (같은 날짜 중복 스킵). 신호 플래그는 semi 섀도에서 병합."""
    if os.path.exists(FORWARD_PATH):
        with open(FORWARD_PATH, encoding='utf-8') as f:
            for line in f:
                try:
                    if json.loads(line).get('date') == today:
                        return {'skipped': True}
                except Exception:
                    continue
    # 신호 — semi 섀도(당일 아침 기록)에서
    sig_mu_sox = sig_sox = None
    shadow = os.path.join(BASE, '..', 'config', 'data', 'semi_shadow.jsonl')
    if os.path.exists(shadow):
        with open(shadow, encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('date') == today and r.get('code') == '005930':
                    sig_mu_sox = r.get('new_signal')
                    sig_sox = r.get('new_signal_v2_sox_only')
    pctile = (breadth or {}).get('atr14_pctile')
    bucket = (0 if pctile is None else min(int(pctile // 20), 4)) + 1  # Q1~Q5
    rec = {'date': today, 'logged_at': datetime.now().isoformat(timespec='seconds'),
           **{k: axes.get(k) for k in ('axis1', 'axis2', 'axis3', 'axis4')},
           'above200': axes.get('above200'), 'ma200_break_days': axes.get('ma200_break_days'),
           'vol_bucket_q': bucket,
           'signal_mu_sox': sig_mu_sox, 'signal_v2_sox_only': sig_sox}
    with open(FORWARD_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec
