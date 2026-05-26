import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token


def parse_sel_fpr_bid(response_data: dict) -> float:
	"""ka10004 응답에서 매도 최우선 호가 (sel_fpr_bid) 추출.

	5/26 probe로 응답 키 확정. 키움이 음수 표기('-298500')로 하락 시 보낼 수 있어
	abs() 절댓값 처리. 부재/빈값/파싱 실패는 0.0.

	Args:
		response_data: ka10004 응답 dict.

	Returns:
		float: 절댓값 호가. 부재/실패 시 0.0.
	"""
	if not isinstance(response_data, dict):
		return 0.0
	raw = response_data.get('sel_fpr_bid', 0)
	if raw is None or raw == '':
		return 0.0
	try:
		return abs(float(raw))
	except (ValueError, TypeError):
		return 0.0


# 주식호가요청
async def fn_ka10004(stk_cd, cont_yn='N', next_key='', token=None, silent=False):
	"""
	주식 호가 요청 함수 (ka10004 TR)
	
	Args:
		stk_cd: 종목코드
		cont_yn: 연속조회여부 (기본값: 'N')
		next_key: 연속조회키 (기본값: '')
		token: API 토큰
		silent: 출력 제어 플래그 (기본값: False)
	
	Returns:
		float: 매도 최우선 호가 (절대값). 오류 시 0 반환
	"""
	try:
		# 1. 요청할 API URL
		endpoint = '/api/dostk/mrkcond'
		url = config.get_host_url() + endpoint

		# 2. header 데이터
		headers = {
			'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
			'authorization': f'Bearer {token}', # 접근토큰
			'cont-yn': cont_yn, # 연속조회여부
			'next-key': next_key, # 연속조회키
			'api-id': 'ka10004', # TR명
		}

		# 3. 요청 데이터
		params = {
			'stk_cd': stk_cd, # 종목코드 거래소별 종목코드 (KRX:039490,NXT:039490_NX,SOR:039490_AL)
		}

		# 4. http POST 요청
		response = await requests.post(url, headers=headers, json=params)
		
		# HTTP 상태 코드 검증
		if response.status_code != 200:
			if not silent:
				print(f"API 호출 실패: HTTP {response.status_code} (종목: {stk_cd})")
			return 0
		
		# JSON 파싱 (예외 처리)
		try:
			response_data = response.json()
		except (json.JSONDecodeError, ValueError) as e:
			if not silent:
				print(f"JSON 파싱 실패 (종목: {stk_cd}): {e}")
			return 0
		
		# parse_sel_fpr_bid 헬퍼 사용 (음수 표기 abs 처리, 부재/실패 시 0.0)
		sel_fpr_bid = parse_sel_fpr_bid(response_data)
		if sel_fpr_bid <= 0:
			if not silent:
				print(f"매도최우선호가가 없거나 0입니다 (종목: {stk_cd}, raw={response_data.get('sel_fpr_bid')!r})")
			return 0

		if not silent:
			print('매도최우선호가(절대값): ', sel_fpr_bid)
			# 5. 응답 상태 코드와 데이터 출력
			print('Code:', response.status_code)
			print('Header:', json.dumps({key: response.headers.get(key) for key in ['next-key', 'cont-yn', 'api-id']}, indent=4, ensure_ascii=False))
			print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

		return sel_fpr_bid
		
	except Exception as e:
		# 모든 예외 상황에서 0 반환 (네트워크 오류, 기타 예외 등)
		if not silent:
			print(f"fn_ka10004 실행 중 오류 발생 (종목: {stk_cd}): {e}")
		return 0

# 실행 구간
if __name__ == '__main__':
	fn_ka10004('005930', token=get_token())

