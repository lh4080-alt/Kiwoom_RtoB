import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

# 주식미체결요청
async def fn_ka10075(stk_cd='', trde_tp='0', stex_tp='0', cont_yn='N', next_key='', token=None):
	"""
	주식 미체결 조회 API (ka10075)
	
	Args:
		stk_cd: 종목코드 (생략 시 전체 조회)
		trde_tp: 매매구분 ('0': 전체, '1': 매도, '2': 매수)
		stex_tp: 거래소구분 ('0': 통합, '1': KRX, '2': NXT)
		cont_yn: 연속조회여부 ('N' or 'Y')
		next_key: 연속조회키
		token: 접근토큰
	
	Returns:
		list: 미체결 주문 리스트 (oso)
	"""
	# 1. 요청할 API URL
	endpoint = '/api/dostk/acnt'
	url = config.get_host_url() + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'cont-yn': cont_yn, # 연속조회여부
		'next-key': next_key, # 연속조회키
		'api-id': 'ka10075', # TR명
	}

	# 3. 요청 데이터 (ka10075 명세에 맞게 재구성)
	params = {
		'all_stk_tp': '1' if stk_cd else '0',  # 전체종목구분: 종목코드가 있으면 '1', 없으면 '0'
		'trde_tp': trde_tp,  # 매매구분: '0': 전체, '1': 매도, '2': 매수
		'stk_cd': stk_cd if stk_cd else '',  # 종목코드: all_stk_tp가 '1'일 때 입력
		'stex_tp': stex_tp,  # 거래소구분: '0': 통합, '1': KRX, '2': NXT
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=params)

	# 5. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	print('Header:', json.dumps({key: response.headers.get(key) for key in ['next-key', 'cont-yn', 'api-id']}, indent=4, ensure_ascii=False))
	response_data = response.json()
	print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	return_code = response_data.get('return_code')
	
	# 미체결 주문 리스트 반환 (ka10075 응답 구조: oso 리스트)
	unfilled_orders = []
	if return_code == 0:
		# ka10075 API는 'oso' 필드에 미체결 주문 리스트를 반환
		unfilled_orders = response_data.get('oso', [])
		if not isinstance(unfilled_orders, list):
			unfilled_orders = []
	
	return unfilled_orders

# 실행 구간
if __name__ == '__main__':
	# 테스트용
	# fn_ka10075(token=get_token())
	pass

