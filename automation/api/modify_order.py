import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

# 주문 정정 (시장가 또는 지정가)
async def fn_sc10001(stk_cd, orgn_ord_no, ord_qty, price=None, token=None):
	"""
	주문 정정 API (시장가 또는 지정가)
	
	Args:
		stk_cd: 종목코드 (예: "005930")
		orgn_ord_no: 원주문번호 (buy_stock 결과에서 저장한 번호)
		ord_qty: 정정할 수량 (미체결 잔량, 문자열)
		price: 정정할 가격 (None 또는 0이면 시장가, 유효한 값이면 지정가)
		token: 접근토큰
	
	Returns:
		return_code: 0이면 성공, 그 외는 실패
	"""
	# 1. 요청할 API URL
	endpoint = '/api/dostk/ordr'
	url = config.get_host_url() + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'api-id': 'kt10002', # TR명 (주식 정정주문)
		'tr_cont': 'N', # 연속조회여부
	}

	# 3. 가격에 따른 거래구분 및 주문단가 결정
	if price is None or price == 0:
		# Case 1: 시장가 주문 (기존 동작)
		trde_tp = '3'  # 시장가
		ord_uv = '0'   # 시장가는 가격 0
	else:
		# Case 2: 지정가 주문
		trde_tp = '0'  # 지정가
		ord_uv = str(int(price))  # 정정할 가격

	# 4. 요청 데이터
	params = {
		'dmst_stex_tp': 'KRX', # 국내거래소구분
		'stk_cd': stk_cd, # 종목코드
		'orgn_ord_no': str(orgn_ord_no), # 원주문번호
		'ord_qty': str(ord_qty), # 정정할 수량 (미체결 잔량)
		'ord_uv': ord_uv, # 주문단가/정정단가 (시장가: '0', 지정가: 실제 가격)
		'trde_tp': trde_tp, # 거래구분 (시장가: '3', 지정가: '0')
		'cond_uv': '', # 조건단가
	}

	# 5. http POST 요청
	response = await requests.post(url, headers=headers, json=params)

	# 6. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	print('Header:', json.dumps({key: response.headers.get(key) for key in ['api-id', 'tr_cont']}, indent=4, ensure_ascii=False))
	response_data = response.json()
	print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	return response_data.get('return_code')

# 실행 구간
if __name__ == '__main__':
	# 테스트용 (실제 사용 시에는 주문번호가 필요)
	# fn_sc10001('005930', '0000123456', '10', token=get_token())
	pass

