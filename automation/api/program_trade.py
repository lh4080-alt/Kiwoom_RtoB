"""종목 일별 프로그램매매 추이 (ka90013).

응답키: stk_daly_prm_trde_trnsn (일별 최대 20개, [0]이 최신).
필드: prm_netprps_amt (프로그램 순매수 금액, 백만원), prm_netprps_qty (수량).
음수 표기: 이중 '--' (예: '--2134262' → -2,134,262).
amt_qty_tp 파라미터 무관 — 한 응답에 amt/qty 둘 다 포함.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests


async def fn_ka90013(stk_cd, token, dt, cont_yn='N', next_key=''):
	"""프로그램매매 일별 추이.

	Args:
		stk_cd: 종목코드
		token: API 토큰
		dt: 기준일 YYYYMMDD
	"""
	endpoint = '/api/dostk/mrkcond'
	url = config.get_host_url() + endpoint
	headers = {
		'Content-Type': 'application/json;charset=UTF-8',
		'authorization': f'Bearer {token}',
		'cont-yn': cont_yn,
		'next-key': next_key,
		'api-id': 'ka90013',
	}
	params = {'stk_cd': stk_cd, 'date': dt}
	response = await requests.post(url, headers=headers, json=params)
	return response.json()
