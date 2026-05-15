import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.check_unfilled import fn_ka10075
from api.cancel_order import fn_sc10002
from utils.stock_code_normalizer import normalize_stock_code

async def cancel_all_unfilled_orders(token=None):
	"""
	모든 미체결 주문을 조회하여 일괄 취소합니다.
	
	Args:
		token: 접근토큰
	
	Returns:
		tuple: (성공한 취소 건수, 실패한 취소 건수, 총 미체결 주문 건수)
	"""
	if not token:
		return (0, 0, 0)
	
	try:
		# 미체결 주문 조회 (전체 조회)
		unfilled_orders = await fn_ka10075(
			stk_cd='',
			trde_tp='0',  # 전체 (매수/매도)
			stex_tp='0',  # 통합 거래소
			token=token
		)
		
		# 로그 출력: 실제 데이터가 담기는지 확인
		print(f"📊 fn_ka10075 반환된 unfilled_orders 리스트 길이: {len(unfilled_orders) if unfilled_orders else 0}")
		if unfilled_orders:
			print(f"📋 첫 번째 주문 샘플: {unfilled_orders[0] if len(unfilled_orders) > 0 else 'N/A'}")
		
		if not unfilled_orders or len(unfilled_orders) == 0:
			return (0, 0, 0)
		
		success_count = 0
		fail_count = 0
		
		# 각 미체결 주문에 대해 순차적으로 취소
		for order in unfilled_orders:
			if not isinstance(order, dict):
				continue
			
			# 주문 정보 추출 (API 명세 기준 필드명 우선 사용)
			# 주문번호: ord_no를 먼저 확인 (조회 응답에서는 ord_no 사용)
			ord_no = order.get('ord_no') or order.get('orgn_ord_no') or order.get('odno')
			# 종목코드: stk_cd
			stk_cd = order.get('stk_cd', '')
			# 미체결수량: oso_qty (명세서상 필드명, 문자열로 올 수 있으므로 int 변환)
			oso_qty_raw = order.get('oso_qty') or order.get('rmn_qty') or order.get('not_qty') or order.get('ord_qty', 0)
			try:
				ord_qty = int(oso_qty_raw) if oso_qty_raw else 0
			except (ValueError, TypeError):
				ord_qty = 0
			
			if not ord_no or not stk_cd or ord_qty <= 0:
				print(f"⚠️ 주문 정보가 불완전합니다: ord_no={ord_no}, stk_cd={stk_cd}, ord_qty={ord_qty}")
				fail_count += 1
				continue
			
			# 거래소 구분 추출 (응답에 없으면 기본값 'KRX' 사용)
			dmst_stex_tp = order.get('dmst_stex_tp') or order.get('stex_tp_nm') or 'KRX'
			# stex_tp가 숫자로 오는 경우 변환 ('1': KRX, '2': NXT)
			if isinstance(dmst_stex_tp, str) and dmst_stex_tp.isdigit():
				if dmst_stex_tp == '1':
					dmst_stex_tp = 'KRX'
				elif dmst_stex_tp == '2':
					dmst_stex_tp = 'NXT'
				else:
					dmst_stex_tp = 'KRX'
			
			# 종목코드 정규화 (키움 API 접두어 'A' 제거)
			stk_cd_clean = normalize_stock_code(stk_cd)
			
			try:
				# 주문 취소 API 호출
				return_code = await fn_sc10002(
					stk_cd=stk_cd_clean,
					orgn_ord_no=ord_no,
					ord_qty=str(ord_qty),
					dmst_stex_tp=dmst_stex_tp,
					token=token
				)
				
				if return_code == 0:
					success_count += 1
					print(f"✅ 주문 취소 성공: {stk_cd_clean} 주문번호 {ord_no} ({ord_qty}주)")
				else:
					fail_count += 1
					print(f"❌ 주문 취소 실패: {stk_cd_clean} 주문번호 {ord_no} (오류 코드: {return_code})")
				
				# Rate Limit 방지를 위한 지연 (다음 주문 취소 전 1초 대기)
				await asyncio.sleep(1)
				
			except Exception as e:
				fail_count += 1
				print(f"❌ 주문 취소 중 오류 발생: {stk_cd_clean} 주문번호 {ord_no} - {e}")
		
		return (success_count, fail_count, len(unfilled_orders))
		
	except Exception as e:
		print(f"❌ 미체결 주문 일괄 취소 중 오류 발생: {e}")
		return (0, 0, 0)

# 실행 구간
if __name__ == '__main__':
	# 테스트용
	pass
