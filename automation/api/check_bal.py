import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

def _to_int(value, default: int = 0) -> int:
	"""API 응답 값(문자열/숫자/None)을 안전하게 int로 변환."""
	try:
		if value is None:
			return default
		if isinstance(value, bool):
			return int(value)
		if isinstance(value, (int, float)):
			return int(value)
		s = str(value).replace(',', '').strip()
		if not s:
			return default
		return int(s)
	except (ValueError, TypeError):
		try:
			s = str(value).replace(',', '').strip()
			return int(float(s)) if s else default
		except (ValueError, TypeError):
			return default

def get_calculatable_balance(balance_data) -> int:
	"""
	예수금 데이터에서 매수 가능 금액을 계산합니다.
	우선순위: d2_entra (1순위) → ord_alowa (2순위) → mny_ord_able_amt (3순위) → ord_alow_amt (4순위) → entr (5순위)
	
	Args:
		balance_data: fn_kt00001이 반환한 response_data (딕셔너리 또는 기타 타입)
	
	Returns:
		int: 계산된 예수금 금액
	"""
	if not isinstance(balance_data, dict):
		return _to_int(balance_data, 0)
	
	# 1. D+2 추정예수금 (매도 대금 포함, 최우선 적용)
	balance = _to_int(balance_data.get('d2_entra', 0))
	
	# 2. 잔고가 0이거나 없으면 주문가능금액(ord_alowa) 확인
	if balance <= 0:
		balance = _to_int(balance_data.get('ord_alowa', 0))
	
	# 3. 대체 필드 확인 (mny_ord_able_amt)
	if balance <= 0:
		balance = _to_int(balance_data.get('mny_ord_able_amt', 0))
	
	# 4. 대체 필드 확인 (ord_alow_amt)
	if balance <= 0:
		balance = _to_int(balance_data.get('ord_alow_amt', 0))
	
	# 5. 최후의 수단: D+0 예수금 (entr)
	if balance <= 0:
		balance = _to_int(balance_data.get('entr', 0))
	
	return balance

# 예수금상세현황요청
async def fn_kt00001(cont_yn='N', next_key='', token=None):
	# 1. 요청할 API URL
	endpoint = '/api/dostk/acnt'
	url = config.get_host_url() + endpoint

	# 1. 토큰 설정
	# token = get_token() # 접근토큰

	# 2. 요청 데이터
	params = {
		'qry_tp': '3', # 조회구분 3:추정조회, 2:일반조회
	}

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'cont-yn': cont_yn, # 연속조회여부
		'next-key': next_key, # 연속조회키
		'api-id': 'kt00001', # TR명
	}

	# 3. http POST 요청
	response = await requests.post(url, headers=headers, json=params)

	# 4. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	response_data = response.json()
	print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	# 필드 확인 및 출력 (entr, d2_entra, ord_alowa)
	if 'entr' in response_data:
		entry = response_data['entr']
		# entr 필드는 문자열(String) 타입이므로 그대로 반환하거나 정수형으로 변환
		if entry:
			try:
				# 문자열을 정수형으로 변환하여 출력
				entr_value = int(entry)
				print(f'예수금(D+0): {entr_value:,}원')
			except (ValueError, TypeError):
				# 변환 실패 시 문자열 그대로 출력
				print(f'예수금(D+0): {entry}원')
	
	# d2_entra 필드 확인 (D+2 추정예수금)
	if 'd2_entra' in response_data:
		d2_entry = response_data['d2_entra']
		if d2_entry:
			try:
				d2_entr_value = int(d2_entry)
				print(f'예수금(D+2): {d2_entr_value:,}원')
			except (ValueError, TypeError):
				print(f'예수금(D+2): {d2_entry}원')
	
	# ord_alowa 필드 확인 (주문가능현금) - 우선순위: ord_alowa > mny_ord_able_amt > ord_alow_amt
	order_able_key = None
	order_able_value = None
	if 'ord_alowa' in response_data:
		order_able_key = 'ord_alowa'
		order_able_value = response_data['ord_alowa']
	elif 'mny_ord_able_amt' in response_data:
		order_able_key = 'mny_ord_able_amt'
		order_able_value = response_data['mny_ord_able_amt']
	elif 'ord_alow_amt' in response_data:
		order_able_key = 'ord_alow_amt'
		order_able_value = response_data['ord_alow_amt']
	
	if order_able_value:
		try:
			order_able_int = int(order_able_value)
			print(f'주문가능현금({order_able_key}): {order_able_int:,}원')
		except (ValueError, TypeError):
			print(f'주문가능현금({order_able_key}): {order_able_value}원')
	
	# 수정됨: 전체 딕셔너리 데이터를 반환하여 호출하는 쪽에서 필요한 필드를 쓰도록 함
	# entr, d2_entra, ord_alowa(또는 mny_ord_able_amt, ord_alow_amt) 필드가 모두 포함됨
	return response_data

# 실행 구간
if __name__ == '__main__':
	fn_kt00001(token=get_token())

