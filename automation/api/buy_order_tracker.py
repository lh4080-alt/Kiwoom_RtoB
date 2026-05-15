"""
매수 주문 추적 모듈
매수 주문 정보(종목코드, 주문번호, 주문시간, 타임아웃 시간 등)를 저장하고 관리
"""
import datetime
from typing import Dict, Optional

class BuyOrderTracker:
	"""매수 주문 추적 클래스"""
	
	def __init__(self):
		# 주문 정보 저장: {종목코드: {주문번호: {order_time, timeout_seconds, original_price, ord_qty}}}
		self.orders: Dict[str, Dict[str, Dict]] = {}
	
	def add_order(self, stk_cd: str, order_no: str, timeout_seconds: int, original_price: float, ord_qty: int):
		"""
		주문 정보를 추가합니다.
		
		Args:
			stk_cd: 종목코드
			order_no: 주문번호
			timeout_seconds: 타임아웃 시간 (초)
			original_price: 원주문 가격
			ord_qty: 주문 수량
		"""
		if stk_cd not in self.orders:
			self.orders[stk_cd] = {}
		
		self.orders[stk_cd][order_no] = {
			'order_time': datetime.datetime.now(),
			'timeout_seconds': timeout_seconds,
			'original_price': original_price,
			'ord_qty': ord_qty
		}
	
	def remove_order(self, stk_cd: str, order_no: str):
		"""
		주문 정보를 제거합니다.
		
		Args:
			stk_cd: 종목코드
			order_no: 주문번호
		"""
		if stk_cd in self.orders and order_no in self.orders[stk_cd]:
			del self.orders[stk_cd][order_no]
			# 종목코드에 대한 주문이 없으면 종목코드도 제거
			if not self.orders[stk_cd]:
				del self.orders[stk_cd]
	
	def get_timed_out_orders(self) -> Dict[str, Dict[str, Dict]]:
		"""
		타임아웃된 주문들을 반환합니다.
		
		Returns:
			{종목코드: {주문번호: {order_time, timeout_seconds, original_price, ord_qty}}}
		"""
		now = datetime.datetime.now()
		timed_out = {}
		
		for stk_cd, orders in self.orders.items():
			for order_no, order_info in orders.items():
				order_time = order_info['order_time']
				timeout_seconds = order_info['timeout_seconds']
				
				# 타임아웃 시간이 지났는지 확인
				elapsed_seconds = (now - order_time).total_seconds()
				if elapsed_seconds >= timeout_seconds:
					if stk_cd not in timed_out:
						timed_out[stk_cd] = {}
					timed_out[stk_cd][order_no] = order_info
		
		return timed_out
	
	def get_all_orders(self) -> Dict[str, Dict[str, Dict]]:
		"""
		모든 주문 정보를 반환합니다.
		
		Returns:
			{종목코드: {주문번호: {order_time, timeout_seconds, original_price, ord_qty}}}
		"""
		return self.orders.copy()
	
	def clear(self):
		"""모든 주문 정보를 제거합니다."""
		self.orders.clear()

# 전역 인스턴스
_tracker = BuyOrderTracker()

def get_tracker() -> BuyOrderTracker:
	"""전역 BuyOrderTracker 인스턴스를 반환합니다."""
	return _tracker

