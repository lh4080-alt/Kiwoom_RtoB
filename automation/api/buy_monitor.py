"""
매수 주문 타임아웃 모니터링 모듈
미체결 주문을 모니터링하고 타임아웃 시 취소 또는 시장가로 전환
"""
import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.get_setting import get_setting
from api.check_unfilled import fn_ka10075
from api.cancel_order import fn_sc10002
from api.modify_order import fn_sc10001
from api.stock_info import fn_ka10001 as stock_info
from telegram.tel_send import tel_send

async def monitor_buy_order(stk_cd, order_no, original_price, ord_qty, timeout_seconds, token):
	"""
	매수 주문 타임아웃 모니터링
	
	Args:
		stk_cd: 종목코드
		order_no: 주문번호
		original_price: 원주문 가격
		ord_qty: 주문 수량
		timeout_seconds: 타임아웃 시간 (초)
		token: 접근토큰
	"""
	try:
		# 타임아웃 대기
		await asyncio.sleep(timeout_seconds)
		
		# 타임아웃 행동 확인
		buy_timeout_action = get_setting('buy_timeout_action', 'cancel')
		
		# 미체결 조회 (ka10075는 orgn_ord_no 파라미터를 지원하지 않으므로 종목코드로 조회 후 필터링)
		unfilled_orders = await fn_ka10075(stk_cd=stk_cd, token=token)
		
		# 헬퍼 함수: 주문번호 추출 (API 명세에 따른 키 조회 순서 준수)
		def get_order_no(order_obj):
			"""주문번호를 우선순위에 따라 추출"""
			return order_obj.get('ord_no') or order_obj.get('orgn_ord_no') or order_obj.get('odno')
		
		# 헬퍼 함수: 미체결 수량 추출 (API 명세에 따른 키 조회 순서 준수)
		def get_quantity(order_obj):
			"""미체결 수량을 우선순위에 따라 추출"""
			return order_obj.get('oso_qty') or order_obj.get('rmn_qty') or order_obj.get('not_qty') or order_obj.get('ord_qty')
		
		# 미체결 주문 찾기
		found = False
		unfilled_qty = 0
		matched_ord_no = None
		
		if unfilled_orders:
			# list와 dict 모두 처리
			iterable_orders = unfilled_orders if isinstance(unfilled_orders, list) else [unfilled_orders]
			
			for order in iterable_orders:
				if not isinstance(order, dict):
					continue
				
				# 1. 주문번호 비교 (우선순위: ord_no > orgn_ord_no > odno)
				current_ord_no = get_order_no(order)
				if current_ord_no:
					current_ord_no_str = str(current_ord_no)
					target_order_no_str = str(order_no)
					
					# 주문번호 비교: 정수형 변환 후 비교 (앞자리 0 패딩 문제 해결)
					order_match = False
					try:
						# 정수형 변환 후 비교
						if int(current_ord_no_str.strip()) == int(target_order_no_str.strip()):
							order_match = True
					except (ValueError, TypeError):
						# 변환 실패 시 기존 방식으로 fallback (공백 제거 후 문자열 비교)
						if current_ord_no_str.strip() == target_order_no_str.strip():
							order_match = True
					
					if order_match:
						# 2. 수량 추출 (우선순위: oso_qty > rmn_qty > not_qty > ord_qty)
						qty_val = get_quantity(order)
						try:
							unfilled_qty = int(qty_val) if qty_val else 0
						except (ValueError, TypeError):
							unfilled_qty = 0
						
						matched_ord_no = current_ord_no_str
						found = True
						break
		
		# 로그 출력: match 여부와 찾은 ord_no, unfilled_qty 명확히 표시
		if found:
			print(f"✅ [타임아웃 모니터] 주문번호 매칭 성공: {stk_cd} - 찾은 주문번호: {matched_ord_no}, 미체결 수량: {unfilled_qty}")
		else:
			print(f"ℹ️ [타임아웃 모니터] 주문번호 매칭 실패: {stk_cd} - 대상 주문번호: {order_no} (이미 체결되었거나 존재하지 않음)")
		
		# 미체결 수량이 없으면 이미 체결된 것으로 간주
		if unfilled_qty <= 0:
			# 체결 완료 (알림 없이 종료)
			return
		
		# 종목명 조회
		try:
			info = await stock_info(stk_cd, token=token)
			stock_name = info.get('stk_nm', stk_cd) if isinstance(info, dict) else stk_cd
		except Exception:
			stock_name = stk_cd
		
		# 타임아웃 행동 실행
		if buy_timeout_action == 'cancel':
			# 취소
			cancel_result = await fn_sc10002(stk_cd, order_no, str(unfilled_qty), token=token)
			if cancel_result == 0:
				message = f"⏰ 타임아웃 발생: {stk_cd} 미체결 수량 취소 처리 완료"
				await tel_send(message)
				print(message)
			else:
				message = f"❌ [타임아웃] {stock_name} 미체결 취소 실패 (오류 코드: {cancel_result})"
				await tel_send(message)
				print(message)
		
		elif buy_timeout_action == 'market':
			# 시장가로 정정
			modify_result = await fn_sc10001(stk_cd, order_no, str(unfilled_qty), token=token)
			if modify_result == 0:
				message = f"⏰ 타임아웃 발생: {stk_cd} 미체결 수량 시장가로 정정 주문 완료"
				await tel_send(message)
				print(message)
			else:
				message = f"❌ [타임아웃] {stock_name} 시장가 정정 실패 (오류 코드: {modify_result})"
				await tel_send(message)
				print(message)
		
	except Exception as e:
		print(f"매수 주문 타임아웃 모니터링 중 오류({stk_cd}, 주문번호: {order_no}): {e}")
		try:
			await tel_send(f"❌ [타임아웃] {stk_cd} 모니터링 중 오류 발생: {e}")
		except:
			pass

