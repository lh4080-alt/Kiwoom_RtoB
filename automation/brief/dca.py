# -*- coding: utf-8 -*-
"""적립식 회차 판정 — 작업 4 (정기 회차·삼전 회차 당김).

주기 (brief_config.DCA_FREQ):
  weekly    월~일 한 주가 한 주기, 정기일 = 그 주 DCA_DAY (휴장이면 다음 KR 거래일 — 다음 주로
            넘어가도 그 주기에 속함)
  biweekly  DCA_ANCHOR부터 14일 단위
  monthly   그 달 첫 DCA_DAY (휴장이면 다음 거래일)
당김: 정기일 이전 같은 주기 안에서 삼전 트리거가 처음 발생한 KR 거래일 1회.
"""
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AUTO)
import brief_config as cfg  # noqa: E402

STATE_PATH = os.path.join(AUTO, '..', 'config', 'data', 'brief', 'brief_state.json')
_WD = {'MON': 0, 'TUE': 1, 'WED': 2, 'THU': 3, 'FRI': 4}


def _next_trading(d: date) -> date:
    from modules.semi_trigger.kr_calendar import is_kr_trading_day, next_kr_trading_day
    return d if is_kr_trading_day(d) else pd.Timestamp(next_kr_trading_day(d)).date()


def _cycle_start(d: date, freq: str = None) -> date:
    freq = freq or cfg.DCA_FREQ
    if freq == 'weekly':
        return d - timedelta(days=d.weekday())
    if freq == 'biweekly':
        a = pd.Timestamp(cfg.DCA_ANCHOR).date()
        a -= timedelta(days=a.weekday())
        return a + timedelta(days=((d - a).days // 14) * 14)
    if freq == 'monthly':
        return d.replace(day=1)
    raise ValueError(freq)


def scheduled_exec(cycle_start: date, freq: str = None) -> date:
    freq = freq or cfg.DCA_FREQ
    wd = _WD[cfg.DCA_DAY]
    if freq in ('weekly', 'biweekly'):
        target = cycle_start + timedelta(days=wd)
    else:
        target = cycle_start + timedelta(days=(wd - cycle_start.weekday()) % 7)
    return _next_trading(target)


def cycle_of(d: date, freq: str = None) -> date:
    """d가 속한 주기 시작일 — 직전 주기 정기일이 휴장으로 d까지 밀린 경우 직전 주기."""
    cur = _cycle_start(d, freq)
    prev = _cycle_start(cur - timedelta(days=1), freq)
    return prev if scheduled_exec(prev, freq) >= d else cur


def pull_status(e: date, trig: bool, state: dict, code: str = '005930') -> str:
    """'pull'(당김 실행) | 'same_day'(정기일 트리거) | 'passed'(정기일 경과) | 'spent'(이번 주기 당김 소진) | 'none'."""
    if not trig:
        return 'none'
    cyc = cycle_of(e)
    sched = scheduled_exec(cyc)
    if e == sched:
        return 'same_day'
    if e > sched:
        return 'passed'
    if state.get('pulled_cycles', {}).get(code) == str(cyc):
        return 'spent'
    return 'pull'


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1, default=str)
    os.replace(STATE_PATH + '.tmp', STATE_PATH)
