"""시장 전체 장마감후 투자자별 매매 (ka10066).

⚠️ 응답 스펙 추정 작성 (Lee 승인) — 첫 실전 1회 후 로그 [ka10066 raw ...]로 필드명/단위 보정.
파싱은 방어적: 외국인=키에 'frgn' 포함, 연기금=키에 'pen' 포함 필드를 스캔.
실패해도 daily_analyzer 시장 라인(등락률)은 그대로 유지됨.

엔드포인트: /api/dostk/mrkcond (시세 계열, ka10004/ka90013와 동일).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests


async def fn_ka10066(mrkt_tp='001', dt='', amt_qty_tp='1', trde_tp='0',
                     stex_tp='3', cont_yn='N', next_key='', token=None):
    """장마감후 투자자별 매매 (시장 전체).

    Args (추정 — 보정 가능):
        mrkt_tp: 시장구분 — '001' 코스피 / '101' 코스닥 (추정)
        dt: 기준일 YYYYMMDD
        amt_qty_tp: '1' 금액 / '2' 수량 (default '1' 금액)
        trde_tp: '0' 순매수
        stex_tp: 거래소구분 — '1' KRX / '2' NXT / '3' 통합 (default '3')
        token: API 토큰 (token_manager 경유 시 자동 주입)
    """
    endpoint = '/api/dostk/mrkcond'
    url = config.get_host_url() + endpoint
    headers = {
        'Content-Type': 'application/json;charset=UTF-8',
        'authorization': f'Bearer {token}',
        'cont-yn': cont_yn,
        'next-key': next_key,
        'api-id': 'ka10066',
    }
    params = {
        'mrkt_tp': mrkt_tp,
        'dt': dt,
        'amt_qty_tp': amt_qty_tp,
        'trde_tp': trde_tp,
        'stex_tp': stex_tp,
    }
    response = await requests.post(url, headers=headers, json=params)
    return response.json()


def _to_float(v):
    """키움 부호 문자열 → float. '-'/'--' 선행 음수, 콤마 제거. 실패 시 None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '')
    if not s:
        return None
    neg = False
    while s and s[0] in '+-':
        if s[0] == '-':
            neg = not neg
        s = s[1:]
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def _iter_records(data):
    """응답에서 레코드 리스트 추출 (dict-of-list / list / flat dict 모두 대응)."""
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return [data]
    if isinstance(data, list):
        return data
    return []


def extract_investor_net(data):
    """응답 → (외국인_순매수, 연기금_순매수) 원시값. 미확보 시 (None, None).

    필드명 추정: 외국인 → 'frgn' 포함 키, 연기금 → 'pen' 포함 키.
    레코드는 [0] (최신/집계 행 추정) 사용.
    """
    records = _iter_records(data)
    if not records:
        return None, None
    rec = records[0]
    if not isinstance(rec, dict):
        return None, None

    frgnr = None
    pension = None
    for k, v in rec.items():
        kl = k.lower()
        if frgnr is None and 'frgn' in kl:
            frgnr = _to_float(v)
        if pension is None and 'pen' in kl:
            pension = _to_float(v)
    return frgnr, pension
