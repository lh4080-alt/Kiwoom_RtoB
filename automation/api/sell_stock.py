import json
import sys
import os
import asyncio

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

# 수동 매도 주문 체결 대기를 위한 이벤트 딕셔너리
# Key: 주문번호 (문자열), Value: asyncio.Event
manual_sell_events = {}

# 주식 매도주문
async def fn_kt10001(stk_cd, ord_qty, cont_yn='N', next_key='', token=None, price=0, order_type='market'):
	"""
	주식 매도 주문
	
	Args:
		stk_cd: 종목코드
		ord_qty: 주문수량
		cont_yn: 연속조회여부
		next_key: 연속조회키
		token: 접근토큰
		price: 주문단가 (지정가 주문 시 사용, 기본값 0)
		order_type: 주문 유형 ('market' 또는 'limit', 기본값 'market')
	
	Returns:
		return_code: 주문 결과 코드
		order_no: 주문번호 (성공 시에만 반환, 실패 시 None)
	"""
	# 1. 요청할 API URL
	endpoint = '/api/dostk/ordr'
	url = config.get_host_url() + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'cont-yn': cont_yn, # 연속조회여부
		'next-key': next_key, # 연속조회키
		'api-id': 'kt10001', # TR명
	}

	# 3. 주문 유형에 따른 trde_tp 및 ord_uv 설정
	if order_type == 'limit':
		# 지정가 주문
		trde_tp = '0'  # 지정가
		if price > 0:
			ord_uv_str = str(int(price))  # 주문단가를 문자열로 변환
		else:
			# 가격이 없으면 시장가로 처리
			trde_tp = '3'
			ord_uv_str = ''
	elif order_type == 'market':
		# 시장가 주문
		trde_tp = '3'  # 시장가
		ord_uv_str = ''  # 시장가는 주문단가 없음 (빈 문자열)
	else:
		# 기본값: 시장가
		trde_tp = '3'
		ord_uv_str = ''

	# 4. 요청 데이터
	params = {
		'dmst_stex_tp': 'KRX', # 국내거래소구분 KRX,NXT,SOR
		'stk_cd': stk_cd, # 종목코드 
		'ord_qty': str(ord_qty), # 주문수량 (문자열로 변환하여 전송)
		'ord_uv': ord_uv_str, # 주문단가 
		'trde_tp': trde_tp, # 매매구분 0:보통 , 3:시장가 , 5:조건부지정가 , 81:장마감후시간외 , 61:장시작전시간외, 62:시간외단일가 , 6:최유리지정가 , 7:최우선지정가 , 10:보통(IOC) , 13:시장가(IOC) , 16:최유리(IOC) , 20:보통(FOK) , 23:시장가(FOK) , 26:최유리(FOK) , 28:스톱지정가,29:중간가,30:중간가(IOC),31:중간가(FOK)
		'cond_uv': '', # 조건단가 
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=params)

	# 5. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	print('Header:', json.dumps({key: response.headers.get(key) for key in ['next-key', 'cont-yn', 'api-id']}, indent=4, ensure_ascii=False))
	response_data = response.json()
	print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	return_code = response_data.get('return_code')
	order_no = None
	
	# 주문번호 추출 (ODNO 키에서 가져오거나, 다른 키에서 찾기)
	if return_code == '0' or return_code == 0:
		# 응답 데이터에서 주문번호 찾기
		order_no = response_data.get('ODNO')
		if order_no is None:
			# 다른 가능한 키들 확인
			order_no = response_data.get('odno') or response_data.get('order_no') or response_data.get('orderNo')
		
		# 주문번호를 문자열로 변환 (앞의 0 제거를 위해 정수 변환 후 다시 문자열로)
		if order_no is not None:
			try:
				# 숫자로 변환 가능하면 정수로 변환 후 문자열로 (앞의 0 제거)
				order_no = str(int(str(order_no).strip()))
			except (ValueError, TypeError):
				# 숫자로 변환 불가능하면 그대로 문자열로
				order_no = str(order_no).strip()
	
	return return_code, order_no

# 실행 구간
if __name__ == '__main__':
	fn_kt10001(stk_cd='005930', ord_qty='1', token=get_token())

