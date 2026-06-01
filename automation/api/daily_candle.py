"""ka10081 — 주식일봉차트조회 (일봉 OHLCV + 거래대금).

semi_trigger ② 단일ETF 자금흐름 (14종 거래대금 합산) + ① 미 메모리 백테스트
(국내 일봉 필요 시) 용도.

응답 키 (키움 OpenAPI 표준):
  stk_dt_pole_chart_qry (list, [0]=최신)
    - dt:         일자 (YYYYMMDD)
    - cur_prc:    종가
    - open_pric:  시가
    - high_pric:  고가
    - low_pric:   저가
    - trde_qty:   거래량 (주)
    - trde_prica: 거래대금 (원)

음수 표기 (예: '-12500')는 절댓값 처리.
"""
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests

logger = logging.getLogger(__name__)

# 요청 간격 제어 (rate limit 보호)
_last_call_time = 0.0
_min_interval = 0.3


def _abs_int(value, default: int = 0) -> int:
	"""음수 표기 부호 제거 후 int 변환. 실패 시 default."""
	if value is None:
		return default
	s = str(value).strip()
	if not s:
		return default
	if s.startswith('-'):
		s = s[1:]
	if s.startswith('+'):
		s = s[1:]
	try:
		return int(s)
	except ValueError:
		try:
			return int(float(s))
		except (ValueError, TypeError):
			return default


def _abs_float(value, default: float = 0.0) -> float:
	if value is None:
		return default
	s = str(value).strip()
	if not s:
		return default
	if s.startswith('-'):
		s = s[1:]
	if s.startswith('+'):
		s = s[1:]
	try:
		return float(s)
	except (ValueError, TypeError):
		return default


def parse_daily_candles(response_data: dict) -> list:
	"""ka10081 응답 → 정규화된 일봉 리스트.

	[{date, open, high, low, close, volume, trade_amount}, ...] (date DESC)
	모두 int. 응답 비정상 시 빈 리스트.
	"""
	if not isinstance(response_data, dict):
		return []
	items = response_data.get('stk_dt_pole_chart_qry', [])
	if not isinstance(items, list):
		return []
	out = []
	for it in items:
		if not isinstance(it, dict):
			continue
		date = str(it.get('dt', '')).strip()
		if not date:
			continue
		out.append({
			'date':         date,
			'open':         _abs_int(it.get('open_pric')),
			'high':         _abs_int(it.get('high_pric')),
			'low':          _abs_int(it.get('low_pric')),
			'close':        _abs_int(it.get('cur_prc')),
			'volume':       _abs_int(it.get('trde_qty')),
			'trade_amount': _abs_int(it.get('trde_prica')),
		})
	return out


async def fn_ka10081(stk_cd: str, base_dt: str, upd_stkpc_tp: str = '1',
                    cont_yn: str = 'N', next_key: str = '',
                    token: str = None, silent: bool = False) -> dict:
	"""주식일봉차트조회.

	Args:
		stk_cd: 종목코드 (6자리, ETF 포함)
		base_dt: 기준일자 YYYYMMDD — 이 날짜로부터 과거 일봉 조회
		upd_stkpc_tp: 수정주가구분 '0' 또는 '1' (기본 '1')
		cont_yn / next_key: 연속조회용
		token: API 토큰
		silent: 출력 억제

	Returns:
		dict {
		  'return_code': int,
		  'candles': [{date, open, high, low, close, volume, trade_amount}, ...],
		  'raw': 원본 응답
		}
	"""
	global _last_call_time
	# rate limit
	now_t = time.time()
	since = now_t - _last_call_time
	if since < _min_interval:
		await asyncio.sleep(_min_interval - since)
	_last_call_time = time.time()

	endpoint = '/api/dostk/chart'
	url = config.get_host_url() + endpoint

	headers = {
		'Content-Type': 'application/json;charset=UTF-8',
		'authorization': f'Bearer {token}',
		'cont-yn': cont_yn,
		'next-key': next_key,
		'api-id': 'ka10081',
	}
	params = {
		'stk_cd': stk_cd,
		'base_dt': base_dt,
		'upd_stkpc_tp': upd_stkpc_tp,
	}

	try:
		response = await requests.post(url, headers=headers, json=params)
		if response.status_code != 200:
			if not silent:
				logger.warning(f"ka10081 HTTP {response.status_code} stk={stk_cd}")
			return {'return_code': -1, 'candles': [], 'raw': {}}
		response_data = response.json()
	except Exception:
		logger.exception(f"ka10081 호출 실패 stk={stk_cd}")
		return {'return_code': -1, 'candles': [], 'raw': {}}

	if not silent:
		print(f"ka10081 {stk_cd} base={base_dt} status={response.status_code} "
		      f"rc={response_data.get('return_code')}")

	candles = parse_daily_candles(response_data)
	return {
		'return_code': response_data.get('return_code'),
		'candles': candles,
		'raw': response_data,
	}
