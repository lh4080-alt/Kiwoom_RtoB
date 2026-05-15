import json
import sys
import os
import asyncio

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.rate_limiter import requests
from api.login import fn_au10001 as get_token
from utils.get_setting import get_setting
from utils.math_helper import calculate_lower_price, calculate_price_by_btp
from api.stock_info import fn_ka10001 as stock_info
from api.buy_monitor import monitor_buy_order

# 주식 매수주문
async def fn_kt10000(stk_cd, ord_qty, ord_uv, cont_yn='N', next_key='', token=None, skip_timeout=False, order_type=None):
	"""
	주식 매수 주문
	
	Args:
		stk_cd: 종목코드
		ord_qty: 주문수량
		ord_uv: 주문단가 (현재가)
		cont_yn: 연속조회여부
		next_key: 연속조회키
		token: 접근토큰
		skip_timeout: 타임아웃 모니터링 건너뛰기 (그리드 트레이딩용)
		order_type: 주문 유형 강제 지정 ('market' 또는 'limit', None이면 설정 파일 사용)
	
	Returns:
		(return_code, order_no): 주문 결과 코드와 주문번호
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
		'api-id': 'kt10000', # TR명
	}

	# 3. 매수 주문 타입 확인 및 가격 계산
	# order_type 파라미터가 전달되면 설정 파일보다 우선순위를 갖음
	if order_type is not None:
		buy_order_type = order_type
	else:
		buy_order_type = get_setting('buy_order_type', 'limit')
	
	original_price = float(ord_uv) if ord_uv and str(ord_uv).strip() != '' and str(ord_uv) != '0' else 0
	btp_value = 0  # BTP 값 (양수: 낮춤, 음수: 높임, 0: 현재가)
	
	if buy_order_type == 'market':
		# 시장가 주문
		trde_tp = '3'  # 시장가
		ord_uv_str = ''  # 시장가는 주문단가 없음 (빈 문자열 또는 '0')
		final_price = 0
	elif buy_order_type == 'limit' or buy_order_type == '0':
		# 보통가(지정가) 주문 - 원래 가격 그대로
		trde_tp = '0'  # 보통
		if original_price > 0:
			ord_uv_str = f'{int(original_price)}'  # 주문단가
			final_price = original_price
		else:
			# 가격이 없으면 시장가로 처리
			trde_tp = '3'
			ord_uv_str = ''
			final_price = 0
	else:
		# 정수 입력: BTP 기반 호가 조정 (order_type이 None일 때만 설정 파일에서 읽은 값 처리)
		if original_price > 0:
			try:
				btp = int(buy_order_type)
				if btp != 0:
					# BTP 기반 가격 계산 (양수: 낮춤, 음수: 높임)
					final_price = calculate_price_by_btp(original_price, btp)
					trde_tp = '0'  # 보통가(지정가)
					ord_uv_str = f'{int(final_price)}'
					btp_value = btp
				else:
					# 0이면 지정가 (현재가)
					trde_tp = '0'
					ord_uv_str = f'{int(original_price)}'
					final_price = original_price
			except (ValueError, TypeError):
				# 파싱 실패 시 기본값 (limit)
				trde_tp = '0'
				ord_uv_str = f'{int(original_price)}'
				final_price = original_price
		else:
			# 가격이 없으면 시장가로 처리
			trde_tp = '3'
			ord_uv_str = ''
			final_price = 0

	# 4. 요청 데이터
	params = {
		'dmst_stex_tp': 'KRX', # 국내거래소구분 KRX,NXT,SOR
		'stk_cd': stk_cd, # 종목코드 
		'ord_qty': f'{ord_qty}', # 주문수량 
		'ord_uv': ord_uv_str, # 주문단가 (시장가는 빈 문자열)
		'trde_tp': trde_tp, # 매매구분 0:보통 , 3:시장가 , 5:조건부지정가 , 81:장마감후시간외 , 61:장시작전시간외, 62:시간외단일가 , 6:최유리지정가 , 7:최우선지정가 , 10:보통(IOC) , 13:시장가(IOC) , 16:최유리(IOC) , 20:보통(FOK) , 23:시장가(FOK) , 26:최유리(FOK) , 28:스톱지정가,29:중간가,30:중간가(IOC),31:중간가(FOK)
		'cond_uv': '', # 조건단가 
	}

	# 5. http POST 요청
	response = await requests.post(url, headers=headers, json=params)

	# 6. 응답 상태 코드와 데이터 출력
	print('Code:', response.status_code)
	print('Header:', json.dumps({key: response.headers.get(key) for key in ['next-key', 'cont-yn', 'api-id']}, indent=4, ensure_ascii=False))
	response_data = response.json()
	print('Body:', json.dumps(response_data, indent=4, ensure_ascii=False))  # JSON 응답을 파싱하여 출력

	return_code = response_data.get('return_code')
	
	# 주문번호 추출 (ODNO)
	order_no = None
	if return_code == 0:
		output = response_data.get('output', {})
		if isinstance(output, dict):
			order_no = output.get('ODNO')
		
		# 주문 접수 알림 (타임아웃 모니터링 시작 전)
		if order_no and not skip_timeout:
			try:
				# 종목명 조회
				info = await stock_info(stk_cd, token=token)
				stock_name = info.get('stk_nm', stk_cd) if isinstance(info, dict) else stk_cd
				
				# 알림 메시지 생성
				if btp_value > 0:
					message = f"📝 [주문 접수] {stock_name} {ord_qty}주 @ {int(final_price):,}원 ({btp_value}호가 낮춤)"
				elif btp_value < 0:
					message = f"📝 [주문 접수] {stock_name} {ord_qty}주 @ {int(final_price):,}원 ({abs(btp_value)}호가 높임)"
				else:
					order_type_str = "시장가" if buy_order_type == 'market' else "지정가"
					message = f"📝 [주문 접수] {stock_name} {ord_qty}주 @ {int(final_price):,}원 ({order_type_str})"
				
				from telegram.tel_send import tel_send
				await tel_send(message)
			except Exception as e:
				print(f"주문 접수 알림 전송 중 오류: {e}")
		
		# 주문 기록 저장 (bto 기능용)
		if order_no:
			try:
				from utils.buy_order_tracker import get_tracker
				tracker = get_tracker()
				tracker.add_order(order_no, stk_cd)
			except Exception as e:
				print(f"주문 기록 저장 중 오류: {e}")
		
		# 기존 타임아웃 모니터링은 비활성화 (새로운 bto 방식으로 대체)
		# 타임아웃 모니터링 시작 (그리드 트레이딩 제외)
		# if order_no and not skip_timeout:
		# 	buy_timeout = get_setting('buy_timeout', 0)
		# 	if buy_timeout > 0:
		# 		# 비동기 태스크로 모니터링 시작
		# 		asyncio.create_task(monitor_buy_order(
		# 			stk_cd=stk_cd,
		# 			order_no=order_no,
		# 			original_price=int(final_price),
		# 			ord_qty=int(ord_qty),
		# 			timeout_seconds=buy_timeout,
		# 			token=token
		# 		))
	
	# (return_code, order_no) 튜플 반환
	return (return_code, order_no)

# 실행 구간
if __name__ == '__main__':
	fn_kt10000('005930', '1', '84200', token=get_token())

