import requests
import json
import sys
from config import real_app_key, real_app_secret, paper_app_key, paper_app_secret, real_host_url, paper_host_url

# 접근토큰 발급
def fn_au10001(is_paper_trading=True):
	# 모드에 따라 설정 선택
	if is_paper_trading:
		app_key = paper_app_key
		app_secret = paper_app_secret
		host_url = paper_host_url
		mode_name = "모의투자"
	else:
		app_key = real_app_key
		app_secret = real_app_secret
		host_url = real_host_url
		mode_name = "실제투자"
	
	print(f"\n{'='*50}")
	print(f"[{mode_name} 모드] 로그인 테스트 시작")
	print(f"{'='*50}")
	
	# 1. 요청할 API URL
	endpoint = '/oauth2/token'
	url =  host_url + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
	}

	# 3. 요청 데이터
	data = {
		'grant_type': 'client_credentials',  # grant_type
		'appkey': app_key,  # 앱키
		'secretkey': app_secret,  # 시크릿키
	}

	# 4. http POST 요청
	response = requests.post(url, headers=headers, json=data)

	# 4. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	print('Body:', json.dumps(response.json(), indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	token = response.json().get('token')
	return token


# 실행 구간
if __name__ == '__main__':
	# 명령줄 인자로 모드 선택 (기본값: 모의투자)
	is_paper = True
	if len(sys.argv) > 1:
		if sys.argv[1].lower() in ['real', '실제', 'false', '0']:
			is_paper = False
	
	token = fn_au10001(is_paper_trading=is_paper)
	if token:
		mode_name = "모의투자" if is_paper else "실제투자"
		print(f"\n✅ [{mode_name} 모드] 로그인에 성공했습니다!")
		print(f"토큰: {token}")
	else:
		mode_name = "모의투자" if is_paper else "실제투자"
		print(f"\n❌ [{mode_name} 모드] 로그인에 실패했습니다.")
		print("app_key와 app_secret을 확인해주세요.")