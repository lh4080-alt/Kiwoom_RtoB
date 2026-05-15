import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

# 일자별종목별실현손익요청_일자
async def fn_ka10072(strt_dt, stk_cd='', cont_yn='N', next_key='', token=None):
	# 1. 요청할 API URL
	endpoint = '/api/dostk/acnt'
	url = config.get_host_url() + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'cont-yn': cont_yn, # 연속조회여부
		'next-key': next_key, # 연속조회키
		'api-id': 'ka10072', # TR명
	}

	# 3. 요청 데이터
	params = {
		'stk_cd': stk_cd if stk_cd else '', # 종목코드 (빈 값이면 전체)
		'strt_dt': strt_dt, # 시작일자 YYYYMMDD
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=params)
	data = response.json()

	# 5. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	print('Header:', json.dumps({key: response.headers.get(key) for key in ['next-key', 'cont-yn', 'api-id']}, indent=4, ensure_ascii=False))
	print('Body:', json.dumps(data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	# 6. 반환값 처리
	return data

# 실행 구간
if __name__ == '__main__':

	fn_ka10072('20241128', '005930', token=get_token())

