import json
import sys
import os
import asyncio
import time

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

# ka10001 요청 간격 제어를 위한 변수
_last_ka10001_call_time = 0
_min_interval = 1


def extract_prev_close(response_data: dict) -> float:
	"""ka10001 응답에서 전일 종가 추출.

	키움 실제 응답 필드는 'base_pric' (예: "299500"). 5/22 GST/HL만도 failed_no_price
	사고 원인 — 봇이 옛 필드명(pred_close_pric 등)만 찾아서 항상 0 반환했음.

	우선순위: base_pric → 4개 legacy 필드 폴백. 모두 없거나 0이면 0.0 반환.
	음수 표기('-294000') 절댓값 처리.
	"""
	raw = (
		response_data.get('base_pric') or
		response_data.get('pred_close_pric') or
		response_data.get('prdy_clpr') or
		response_data.get('bfdy_clpr') or
		response_data.get('prev_close_price')
	)
	if isinstance(raw, str) and raw.startswith('-'):
		raw = raw[1:]
	try:
		return float(raw) if raw else 0.0
	except (ValueError, TypeError):
		return 0.0


# 주식기본정보요청
async def fn_ka10001(stk_cd, cont_yn='N', next_key='', token=None, silent=False):
	global _last_ka10001_call_time
	
	# 연속 요청 간격 제어: 마지막 호출로부터 1초가 지나지 않았다면 대기
	current_time = time.time()
	time_since_last_call = current_time - _last_ka10001_call_time
	if time_since_last_call < _min_interval:
		wait_time = _min_interval - time_since_last_call
		await asyncio.sleep(wait_time)
	
	_last_ka10001_call_time = time.time()
	# 1. 요청할 API URL
	endpoint = '/api/dostk/stkinfo'
	url = config.get_host_url() + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'cont-yn': cont_yn, # 연속조회여부
		'next-key': next_key, # 연속조회키
		'api-id': 'ka10001', # TR명
	}

	# 3. 요청 데이터
	params = {
		'stk_cd': stk_cd, # 종목코드 거래소별 종목코드 (KRX:039490,NXT:039490_NX,SOR:039490_AL)
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=params)
	response_data = response.json()

	if not silent:
		# 4. 응답 상태 코드와 데이터 출력
		print('Code:', response.status_code)
		print('Header:', json.dumps({key: response.headers.get(key) for key in ['next-key', 'cont-yn', 'api-id']}, indent=4, ensure_ascii=False))
		body_str = json.dumps(response_data, indent=4, ensure_ascii=False)
		body_lines = body_str.split('\n')
		print('Body:', '\n'.join(body_lines[:3]))  # JSON 응답을 파싱하여 첫 3줄만 출력

	# cur_prc 추출 및 반환
	cur_prc = response_data.get('cur_prc', '0')
	# 음수로 오는 경우가 있으므로 절댓값 처리
	if isinstance(cur_prc, str) and cur_prc.startswith('-'):
		cur_prc = cur_prc[1:]
	try:
		cur_prc = float(cur_prc) if cur_prc else 0.0
	except (ValueError, TypeError):
		cur_prc = 0.0
	
	prev_close_price = extract_prev_close(response_data)
	
	return {
		'stk_nm': response_data.get('stk_nm', ''),
		'cur_prc': cur_prc,
		'prev_close_price': prev_close_price
	}

# 현재가 조회 함수 (분할 트레이딩용 래퍼)
async def get_current_price(code, token=None):
	"""
	종목의 현재가를 조회합니다.
	
	Args:
		code: 종목코드
		token: API 토큰
	
	Returns:
		float: 현재가 (오류 시 0.0)
	"""
	try:
		info = await fn_ka10001(code, token=token, silent=True)
		return info.get('cur_prc', 0.0) if isinstance(info, dict) else 0.0
	except Exception as e:
		print(f"get_current_price 오류 ({code}): {e}")
		return 0.0

# 주식 기본정보 조회 함수 (srch 명령어용)
async def get_stock_info(stock_code, token=None):
	"""
	종목의 기본 정보를 조회합니다 (ka10001 API).
	
	Args:
		stock_code: 종목코드 (6자리 문자열, 예: "005930")
		token: API 토큰 (없으면 자동 발급)
	
	Returns:
		dict: 종목 정보 딕셔너리 또는 None (오류 시)
		주요 필드:
			- stk_nm: 종목명
			- cur_prc: 현재가 (String)
			- flu_rt: 등락률 (String)
			- pred_pre: 전일 대비 (String)
			- trde_qty: 거래량 (String)
			- high_pric: 고가
			- low_pric: 저가
			- open_pric: 시가
	"""
	global _last_ka10001_call_time
	
	# 토큰이 없으면 발급
	if not token:
		try:
			token = await get_token()
			if not token:
				print("토큰 발급 실패")
				return None
		except Exception as e:
			print(f"토큰 발급 중 오류: {e}")
			return None
	
	# 연속 요청 간격 제어
	current_time = time.time()
	time_since_last_call = current_time - _last_ka10001_call_time
	if time_since_last_call < _min_interval:
		wait_time = _min_interval - time_since_last_call
		await asyncio.sleep(wait_time)
	
	_last_ka10001_call_time = time.time()
	
	# 1. 요청할 API URL
	endpoint = '/api/dostk/stkinfo'
	url = config.get_host_url() + endpoint
	
	# 2. header 데이터 (지시서에 따라 appkey, appsecret 포함)
	headers = {
		'Content-Type': 'application/json;charset=UTF-8',
		'Authorization': f'Bearer {token}',
		'appkey': config.get_app_key(),
		'appsecret': config.get_app_secret(),
		'api-id': 'ka10001',
	}
	
	# 3. 요청 데이터
	params = {
		'stk_cd': stock_code,
	}
	
	try:
		# 4. http POST 요청
		response = await requests.post(url, headers=headers, json=params)
		
		# 응답 코드 확인
		if response.status_code != 200:
			print(f"API 응답 오류: {response.status_code}")
			return None
		
		response_data = response.json()
		
		# 에러 체크 (return_code가 있으면 확인)
		return_code = response_data.get('return_code')
		if return_code is not None and return_code != 0:
			print(f"API 반환 코드 오류: {return_code}")
			return None
		
		# 전체 응답 데이터 반환
		return response_data
		
	except Exception as e:
		print(f"get_stock_info 오류 ({stock_code}): {e}")
		return None

# 실행 구간
if __name__ == '__main__':
	fn_ka10001('138610', token=get_token())

