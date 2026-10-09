# -*- coding: utf-8 -*-
"""V3 골든 테스트 — 브리프 회귀 방지 (픽스처는 V1·V2 대조 통과 값만 고정).

· 고정 d → render 텍스트 완전 일치, V4 무결성 결과 일치
· 세션 판정(순수 로직): 10/8 평일, 10/9 한글날(예비 1/2), 10/10 토(확정 2/2), 서머타임 해제 후
"""
import glob
import json
import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'automation'))
from brief.build import KST, render, session_frame  # noqa: E402
from brief.integrity import run_checks  # noqa: E402
from brief.serde import loads  # noqa: E402

FIX = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures',
                                    'brief_*.json')))


@pytest.mark.parametrize('path', FIX, ids=[os.path.basename(p) for p in FIX])
def test_render_golden(path):
    fx = json.load(open(path, encoding='utf-8'))
    d = loads(json.dumps(fx['d']))
    assert render(d, {}, fx['fails']) == fx['text']
    assert run_checks(d) == fx['fails']
    assert all(r['pass'] for r in fx['verification'])


@pytest.mark.parametrize('now,us_last,exec_d,closed,total,final', [
    # 평일: 미국 10/7 세션 → KR 10/8
    ('2026-10-08T05:35', date(2026, 10, 7), date(2026, 10, 8), 1, 1, True),
    # 한글날(10/9 KR 휴장) 아침: 미국 10/8만 마감, 10/9 세션 남음 → 예비 1/2
    ('2026-10-09T05:35', date(2026, 10, 8), date(2026, 10, 12), 1, 2, False),
    # 토요일 아침: 미국 10/9 마감 → 확정 2/2
    ('2026-10-10T05:35', date(2026, 10, 9), date(2026, 10, 12), 2, 2, True),
    # 일요일: 새 세션 없음 → 직전과 동일 판정 (러너가 중복 스킵)
    ('2026-10-11T05:35', date(2026, 10, 9), date(2026, 10, 12), 2, 2, True),
    # 서머타임 해제 후(11/5 목, EST): 05:35엔 11/4 세션까지만 마감
    ('2026-11-06T05:35', date(2026, 11, 4), date(2026, 11, 5), 1, 1, True),
    ('2026-11-06T06:35', date(2026, 11, 5), date(2026, 11, 6), 1, 1, True),
])
def test_session_frame(now, us_last, exec_d, closed, total, final):
    sf = session_frame(datetime.fromisoformat(now).replace(tzinfo=KST))
    assert sf['d_last'] == us_last
    assert sf['exec'] == exec_d
    assert (len(sf['closed']), sf['total'], sf['final']) == (closed, total, final)
    assert sf['send_at'] <= datetime.fromisoformat(now).replace(tzinfo=KST)


def test_send_time_dst():
    """미국 마감+30분 — 서머타임 05:30, 표준시 06:30 (zoneinfo 자동)."""
    sf = session_frame(datetime(2026, 10, 9, 5, 35, tzinfo=KST))
    assert (sf['send_at'].hour, sf['send_at'].minute) == (5, 30)
    sf = session_frame(datetime(2026, 11, 6, 6, 35, tzinfo=KST))
    assert (sf['send_at'].hour, sf['send_at'].minute) == (6, 30)
