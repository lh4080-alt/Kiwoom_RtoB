"""체결강도 (ka10046).

응답키: cntr_str_tm (1분 단위 60개, [0]이 최신).
필드: cntr_str (실시간), cntr_str_5min/20min/60min (이동평균).
음수 표기: 단일 '-' (가격 등).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests


async def fn_ka10046(stk_cd, token, cont_yn='N', next_key=''):
	"""체결강도 분단위 데이터. raw response_json 반환 (호출자가 파싱)."""
	endpoint = '/api/dostk/mrkcond'
	url = config.get_host_url() + endpoint
	headers = {
		'Content-Type': 'application/json;charset=UTF-8',
		'authorization': f'Bearer {token}',
		'cont-yn': cont_yn,
		'next-key': next_key,
		'api-id': 'ka10046',
	}
	params = {'stk_cd': stk_cd}
	response = await requests.post(url, headers=headers, json=params)
	return response.json()
