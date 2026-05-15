import pandas as pd
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token

# 계좌평가현황요청
async def fn_kt00004(print_df=False, cont_yn='N', next_key='', token=None):
	# 1. 요청할 API URL
	endpoint = '/api/dostk/acnt'
	url = config.get_host_url() + endpoint

	# 2. header 데이터
	headers = {
		'Content-Type': 'application/json;charset=UTF-8', # 컨텐츠타입
		'authorization': f'Bearer {token}', # 접근토큰
		'cont-yn': cont_yn, # 연속조회여부
		'next-key': next_key, # 연속조회키
		'api-id': 'kt00004', # TR명
	}

	# 3. 요청 데이터
	params = {
		'qry_tp': '0', # 상장폐지조회구분 0:전체, 1:상장폐지종목제외
		'dmst_stex_tp': 'KRX', # 국내거래소구분 KRX:한국거래소,NXT:넥스트트레이드
	}

	# 4. http POST 요청
	response = await requests.post(url, headers=headers, json=params)
	response_data = response.json()
	
	# 응답 코드 우선 검증
	return_code = response_data.get('return_code', None)
	
	# return_code가 정상("0" 또는 0)이 아닌 경우
	if return_code != "0" and return_code != 0:
		return_msg = response_data.get('return_msg', '알 수 없는 오류')
		print(f"API 호출 예외: {return_msg} (return_code: {return_code})")
		return []
	
	# 안전한 데이터 접근: .get() 메서드를 사용하여 키가 없을 경우 빈 리스트 반환
	stk_acnt_evlt_prst = response_data.get('stk_acnt_evlt_prst', [])
	if not stk_acnt_evlt_prst:
		return []

	if print_df:
		df = pd.DataFrame(stk_acnt_evlt_prst)[['stk_cd', 'stk_nm', 'pl_rt', 'rmnd_qty']]
		pd.set_option('display.unicode.east_asian_width', True)
		print(df.to_string(index=False))

	return stk_acnt_evlt_prst

# 실행 구간
if __name__ == '__main__':
	fn_kt00004(True, token=get_token())

