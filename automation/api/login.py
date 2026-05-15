import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests

# 접근토큰 발급
async def fn_au10001():
	# 1. 요청할 API URL
	endpoint = '/oauth2/token'
	url = config.get_host_url() + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
	}

# 3. 요청 데이터
	data = {
		'grant_type': 'client_credentials',  # grant_type
		'appkey': config.get_app_key(),  # 앱키
		'secretkey': config.get_app_secret(),  # 시크릿키
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=data)

	# 4. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	response_data = response.json()
	print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	token = response_data.get('token')
	return token


# 실행 구간
if __name__ == '__main__':
	token = fn_au10001()
	print("토큰: ",token)

