"""종목별 외인/기관/개인 일별 매매 (ka10059).

응답키: stk_invsr_orgn (일별 최대 100개, [0]이 최신).
필드: frgnr_invsr (외국인), orgn (기관), ind_invsr (개인), fnnc_invt (금융투자) 등.
단위: amt_qty_tp=2 + unit_tp=1000 → 천주.
음수 표기: 단일 '-' (예: '-9283').
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests


async def fn_ka10059(stk_cd, token, dt, amt_qty_tp='2', trde_tp='0', unit_tp='1000', cont_yn='N', next_key=''):
	"""외인/기관/개인 일별 매매.

	Args:
		stk_cd: 종목코드 (6자리)
		token: API 토큰
		dt: 기준일 YYYYMMDD (최근 거래일)
		amt_qty_tp: '1' 금액 / '2' 수량 (default '2')
		trde_tp: '0' 순매수 (default '0')
		unit_tp: '1000' 천주 / '1' 단주 (default '1000')
	"""
	endpoint = '/api/dostk/stkinfo'
	url = config.get_host_url() + endpoint
	headers = {
		'Content-Type': 'application/json;charset=UTF-8',
		'authorization': f'Bearer {token}',
		'cont-yn': cont_yn,
		'next-key': next_key,
		'api-id': 'ka10059',
	}
	params = {
		'stk_cd': stk_cd,
		'dt': dt,
		'amt_qty_tp': amt_qty_tp,
		'trde_tp': trde_tp,
		'unit_tp': unit_tp,
	}
	response = await requests.post(url, headers=headers, json=params)
	return response.json()
