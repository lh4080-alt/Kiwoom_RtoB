import sys
import os
import asyncio
import time

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token
import json

# ka10080 요청 간격 제어를 위한 변수
_last_ka10080_call_time = 0
_min_interval = 1

# 주식분봉차트조회요청
async def fn_ka10080(stk_cd, tic_scope, cont_yn='N', next_key='', token=None):
	global _last_ka10080_call_time
	
	# 연속 요청 간격 제어: 마지막 호출로부터 0.25초가 지나지 않았다면 대기
	current_time = time.time()
	time_since_last_call = current_time - _last_ka10080_call_time
	if time_since_last_call < _min_interval:
		wait_time = _min_interval - time_since_last_call
		await asyncio.sleep(wait_time)
	
	_last_ka10080_call_time = time.time()
	
	# 1. 요청할 API URL
	url = config.get_host_url() + '/api/dostk/chart'

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'cont-yn': cont_yn, # 연속조회여부
		'next-key': next_key, # 연속조회키
		'api-id': 'ka10080', # TR명
	}

	# 3. 요청 데이터
	params = {
		'stk_cd': stk_cd, # 종목코드 거래소별 종목코드 (KRX:039490,NXT:039490_NX,SOR:039490_AL)
		'tic_scope': str(tic_scope), # 틱범위 1:1분, 3:3분, 5:5분, 10:10분, 15:15분, 30:30분, 45:45분, 60:60분
		'upd_stkpc_tp': '1', # 수정주가구분 0 or 1
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=params)
	data = response.json()
	chart_data = data.get('stk_min_pole_chart_qry', [])

	# 5. [수정] 응답 로그 간소화

	if response.status_code == 200:

		count = len(chart_data)

		latest_prc = '0'

		if count > 0:

			# 가장 최신 데이터의 현재가 가져오기

			latest_prc = chart_data[0].get('cur_prc', '0')

			# 하락 종목의 경우 가격 앞에 '-'가 붙으므로 제거하여 출력

			if latest_prc.startswith('-'):

				latest_prc = latest_prc[1:]

		

		print(f"📊 [분봉차트] {stk_cd} ({tic_scope}분) | 수신: {count}개 | 최신가: {latest_prc}")

	else:

		print(f"📊 [분봉차트] {stk_cd} 요청 실패 | 상태코드: {response.status_code}")

	# 6. 반환값 처리 - 가장 최신 봉의 cur_prc 반환
	if chart_data and len(chart_data) > 0:
		latest_candle = chart_data[0]  # 첫 번째가 가장 최신
		cur_prc = latest_candle.get('cur_prc', '0')
		# 음수로 오는 경우가 있으므로 절댓값 처리
		if cur_prc.startswith('-'):
			cur_prc = cur_prc[1:]
		return float(cur_prc) if cur_prc else 0.0
	return 0.0

# 실행 구간
if __name__ == '__main__':

	fn_ka10080('005930', 5, token=get_token())

