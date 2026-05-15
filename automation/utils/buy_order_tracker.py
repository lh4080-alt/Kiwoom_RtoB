"""
매수 주문 추적 모듈
bto 기능을 위해 매수 주문의 시간과 종목코드를 기록합니다.
"""
import os
import json
import datetime
import threading

class BuyOrderTracker:
	"""매수 주문 추적 클래스"""
	
	_instance = None
	_lock = threading.Lock()
	
	def __new__(cls):
		if cls._instance is None:
			with cls._lock:
				if cls._instance is None:
					cls._instance = super(BuyOrderTracker, cls).__new__(cls)
					cls._instance._initialized = False
		return cls._instance
	
	def __init__(self):
		if self._initialized:
			return
		
		self._initialized = True
		self._lock = threading.Lock()
		self._orders = {}  # {order_no: {'stk_cd': str, 'order_time': datetime.datetime}}
		
		# 데이터 파일 경로
		script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		base_dir = os.path.dirname(script_dir)
		config_dir = os.path.join(base_dir, 'config')
		data_dir = os.path.join(config_dir, 'data')
		self.data_file = os.path.join(data_dir, 'buy_orders.json')
		
		# data 폴더가 없으면 생성
		if not os.path.exists(data_dir):
			os.makedirs(data_dir, exist_ok=True)
		
		# 기존 데이터 로드
		self._load_orders()
	
	def _load_orders(self):
		"""파일에서 주문 기록 로드"""
		try:
			if os.path.exists(self.data_file):
				with open(self.data_file, 'r', encoding='utf-8') as f:
					data = json.load(f)
					# 문자열 시간을 datetime으로 변환
					for order_no, order_info in data.items():
						order_time_str = order_info.get('order_time')
						if order_time_str:
							try:
								order_time = datetime.datetime.fromisoformat(order_time_str)
								self._orders[order_no] = {
									'stk_cd': order_info.get('stk_cd', ''),
									'order_time': order_time
								}
							except (ValueError, TypeError):
								# 파싱 실패 시 현재 시간으로 대체
								self._orders[order_no] = {
									'stk_cd': order_info.get('stk_cd', ''),
									'order_time': datetime.datetime.now()
								}
		except Exception as e:
			print(f"주문 기록 로드 중 오류: {e}")
			self._orders = {}
	
	def _save_orders(self):
		"""주문 기록을 파일에 저장"""
		try:
			# datetime을 문자열로 변환
			data = {}
			for order_no, order_info in self._orders.items():
				data[order_no] = {
					'stk_cd': order_info.get('stk_cd', ''),
					'order_time': order_info.get('order_time').isoformat() if isinstance(order_info.get('order_time'), datetime.datetime) else datetime.datetime.now().isoformat()
				}
			
			with open(self.data_file, 'w', encoding='utf-8') as f:
				json.dump(data, f, ensure_ascii=False, indent=2)
		except Exception as e:
			print(f"주문 기록 저장 중 오류: {e}")
	
	def add_order(self, order_no, stk_cd):
		"""주문 기록 추가"""
		if not order_no or not stk_cd:
			return False
		
		with self._lock:
			order_no_str = str(order_no).strip()
			self._orders[order_no_str] = {
				'stk_cd': str(stk_cd).strip(),
				'order_time': datetime.datetime.now()
			}
			self._save_orders()
			return True
	
	def get_order(self, order_no):
		"""주문 정보 조회"""
		with self._lock:
			order_no_str = str(order_no).strip()
			return self._orders.get(order_no_str)
	
	def is_tracked_order(self, order_no):
		"""주문이 추적 중인지 확인"""
		with self._lock:
			order_no_str = str(order_no).strip()
			return order_no_str in self._orders
	
	def remove_order(self, order_no):
		"""주문 기록 제거 (체결 완료 등)"""
		with self._lock:
			order_no_str = str(order_no).strip()
			if order_no_str in self._orders:
				del self._orders[order_no_str]
				self._save_orders()
				return True
			return False
	
	def cleanup_old_orders(self, max_age_hours=24):
		"""오래된 주문 기록 정리 (기본 24시간 이상 된 기록 삭제)"""
		with self._lock:
			now = datetime.datetime.now()
			orders_to_remove = []
			
			for order_no, order_info in self._orders.items():
				order_time = order_info.get('order_time')
				if isinstance(order_time, datetime.datetime):
					age = now - order_time
					if age.total_seconds() > max_age_hours * 3600:
						orders_to_remove.append(order_no)
			
			for order_no in orders_to_remove:
				del self._orders[order_no]
			
			if orders_to_remove:
				self._save_orders()
				print(f"오래된 주문 기록 {len(orders_to_remove)}개를 정리했습니다.")
			
			return len(orders_to_remove)

# 싱글톤 인스턴스
_tracker = None

def get_tracker():
	"""BuyOrderTracker 싱글톤 인스턴스 반환"""
	global _tracker
	if _tracker is None:
		_tracker = BuyOrderTracker()
	return _tracker

