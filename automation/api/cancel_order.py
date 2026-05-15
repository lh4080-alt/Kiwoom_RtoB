import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

# 주문 취소
async def fn_sc10002(stk_cd, orgn_ord_no, ord_qty, dmst_stex_tp='KRX', token=None):
	"""
	주문 취소 API (kt10003)
	
	Args:
		stk_cd: 종목코드 (예: "005930")
		orgn_ord_no: 원주문번호 (buy_stock 결과에서 저장한 번호)
		ord_qty: 취소할 수량 (문자열)
		dmst_stex_tp: 거래소 구분 ('KRX', 'NXT', 'SOR' 등, 기본값: 'KRX')
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
		'api-id': 'kt10003', # TR명 (주식 취소주문)
		'tr_cont': 'N', # 연속조회여부
	}

	# 3. 요청 데이터
	params = {
		'dmst_stex_tp': dmst_stex_tp, # 국내거래소구분
		'stk_cd': stk_cd, # 종목코드
		'orig_ord_no': str(orgn_ord_no), # 원주문번호 (API 명세에 맞게 orig_ord_no로 변경)
		'cncl_qty': str(ord_qty), # 취소할 수량 (API 명세에 맞게 cncl_qty로 변경)
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=params)

	# 5. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	print('Header:', json.dumps({key: response.headers.get(key) for key in ['api-id', 'tr_cont']}, indent=4, ensure_ascii=False))
	response_data = response.json()
	print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	return response_data.get('return_code')

# 실행 구간
if __name__ == '__main__':
	# 테스트용 (실제 사용 시에는 주문번호가 필요)
	# fn_sc10002('005930', '0000123456', '10', token=get_token())
	pass

