import json
import os
import sys
import asyncio
import time
from datetime import datetime

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.stock_info import get_current_price
from api.buy_stock import fn_kt10000 as buy_stock
from api.sell_stock import fn_kt10001 as sell_stock
from api.acc_val import fn_kt00004
from telegram.tel_send import tel_send
from api.login import fn_au10001 as get_token
from utils.stock_code_normalizer import normalize_stock_code

# 종목별 마지막 체크 시간 (쿨타임 관리)
_last_check_times = {}

def get_wave_config_path():
	"""wave_config.json 파일 경로를 반환합니다."""
	script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'wave_config.json')

def get_wave_status_path():
	"""wave_status.json 파일 경로를 반환합니다."""
	script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'wave_status.json')

def load_wave_config():
	"""wave_config.json 파일을 로드합니다."""
	file_path = get_wave_config_path()
	if os.path.exists(file_path):
		try:
			with open(file_path, 'r', encoding='utf-8') as f:
				return json.load(f)
		except Exception as e:
			print(f"wave_config.json 로드 실패: {e}")
			return {
				"buy_ratios": [0.33, 0.33, 0.34],
				"sell_ratios": [0.33, 0.33, 0.34],
				"buy_steps": [5.0, 10.0, 3.0],
				"sell_steps": [5.0, 10.0, 3.0]
			}
	return {
		"buy_ratios": [0.33, 0.33, 0.34],
		"sell_ratios": [0.33, 0.33, 0.34],
		"buy_steps": [5.0, 10.0, 3.0],
		"sell_steps": [5.0, 10.0, 3.0]
	}

def load_wave_status():
	"""wave_status.json 파일을 로드합니다."""
	file_path = get_wave_status_path()
	if os.path.exists(file_path):
		try:
			with open(file_path, 'r', encoding='utf-8') as f:
				return json.load(f)
		except Exception as e:
			print(f"wave_status.json 로드 실패: {e}")
			return {}
	return {}

def save_wave_status(data):
	"""wave_status.json 파일에 저장합니다."""
	file_path = get_wave_status_path()
	try:
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, indent=2, ensure_ascii=False)
		return True
	except Exception as e:
		print(f"wave_status.json 저장 실패: {e}")
		return False

async def get_holding_quantity(code, token):
	"""보유 수량을 조회합니다."""
	try:
		my_stocks = await fn_kt00004(False, 'N', '', token)
		if not my_stocks:
			return 0
		
		for stock in my_stocks:
			stock_code = normalize_stock_code(stock.get('stk_cd', ''))
			if stock_code == code:
				rmnd_qty = stock.get('rmnd_qty', '0')
				try:
					return int(rmnd_qty) if rmnd_qty else 0
				except (ValueError, TypeError):
					return 0
		return 0
	except Exception as e:
		print(f"보유 수량 조회 오류 ({code}): {e}")
		return 0

async def get_holding_avg_price(code, token):
	"""보유 종목의 평균 단가를 조회합니다."""
	try:
		my_stocks = await fn_kt00004(False, 'N', '', token)
		if not my_stocks:
			return 0.0
		
		for stock in my_stocks:
			stock_code = normalize_stock_code(stock.get('stk_cd', ''))
			if stock_code == code:
				avg_prc = stock.get('avg_prc', '0')
				try:
					return float(avg_prc) if avg_prc else 0.0
				except (ValueError, TypeError):
					return 0.0
		return 0.0
	except Exception as e:
		print(f"평균 단가 조회 오류 ({code}): {e}")
		return 0.0

async def sync_holding_data(code, status, token):
	"""실제 계좌 데이터와 wave_status.json의 데이터를 동기화합니다."""
	try:
		my_stocks = await fn_kt00004(False, 'N', '', token)
		if not my_stocks:
			return False
		
		for stock in my_stocks:
			stock_code = normalize_stock_code(stock.get('stk_cd', ''))
			if stock_code == code:
				# 실제 계좌 데이터
				real_avg_price = float(stock.get('avg_prc', '0') or 0)
				real_qty = int(stock.get('rmnd_qty', '0') or 0)
				
				# wave_status.json의 데이터
				stored_avg_price = status.get('avg_price', 0)
				stored_qty = status.get('total_qty', 0)
				
				# 데이터 불일치 시 동기화
				if real_qty > 0:
					if abs(real_avg_price - stored_avg_price) > 0.01 or real_qty != stored_qty:
						print(f"[Wave 동기화] {code}: 평단가 {stored_avg_price:,.0f} → {real_avg_price:,.0f}, 수량 {stored_qty} → {real_qty}")
						status['avg_price'] = real_avg_price
						status['total_qty'] = real_qty
						status['holding_qty'] = real_qty  # 실제 보유 수량 저장
						return True
				else:
					# 보유 수량이 0이면 상태 초기화
					if stored_qty > 0:
						print(f"[Wave 동기화] {code}: 보유 수량이 0이 되었습니다. 상태 초기화.")
						status['total_qty'] = 0
						status['avg_price'] = 0
						status['holding_qty'] = 0
						return True
		return False
	except Exception as e:
		print(f"[Wave 동기화] 오류 ({code}): {e}")
		return False

class WaveManager:
	def __init__(self):
		self.token = None
		self.processing_stocks = set()  # 처리 중인 종목 관리
	
	async def check_and_execute(self, token=None):
		"""주기적으로 호출되어 감시 및 매매를 실행합니다."""
		if token:
			self.token = token
		
		if not self.token:
			# 토큰이 없으면 자동으로 발급 시도
			try:
				self.token = await get_token()
			except Exception as e:
				print(f"토큰 발급 실패: {e}")
				return
		
		# wave_status.json 로드
		status_data = load_wave_status()
		if not status_data:
			return
		
		# 종목별로 체크 (쿨타임 적용)
		current_time = time.time()
		for code, status in list(status_data.items()):
			# 이미 처리 중인 종목이면 스킵
			if code in self.processing_stocks:
				continue
			
			# 종목별 쿨타임 체크 (Step 2에서는 더 짧은 쿨타임)
			step_index = status.get('step_index', 0)
			phase = status.get('current_phase', 'BUY')
			
			# Step 2 (3차 매수/매도 대기)에서는 쿨타임을 0.5초로 단축
			if step_index == 2:
				cooldown = 0.5
			else:
				cooldown = 1.5
			
			last_check = _last_check_times.get(code, 0)
			if current_time - last_check < cooldown:
				continue
			
			_last_check_times[code] = current_time
			
			# 처리 시작 표시
			self.processing_stocks.add(code)
			
			# 비동기로 처리 (블로킹 방지)
			task = asyncio.create_task(self._process_stock(code, status, status_data))
			
			# 태스크 완료 시 목록에서 제거 (람다 함수 활용)
			task.add_done_callback(lambda t, c=code: self.processing_stocks.discard(c))
	
	async def _process_stock(self, code, status, status_data):
		"""개별 종목을 처리합니다."""
		try:
			current_price = await get_current_price(code, self.token)
			if current_price <= 0:
				return
			
			# 실제 계좌 데이터와 동기화
			await sync_holding_data(code, status, self.token)
			
			phase = status.get('current_phase', 'BUY')
			step_index = status.get('step_index', 0)
			
			if phase == 'BUY':
				await self._process_buy_phase(code, status, status_data, current_price)
			elif phase == 'SELL':
				await self._process_sell_phase(code, status, status_data, current_price)
			
			# 강제 전환 조건 체크 (BUY 모드에서 익절가 도달 시 SELL로 전환)
			# avg_price가 0이어도 base_price 기준으로 1차 익절가 체크
			if phase == 'BUY':
				config = load_wave_config()
				sell_steps = config.get('sell_steps', [5.0, 10.0, 3.0])
				avg_price = status.get('avg_price', 0)
				base_price = status.get('base_price', 0)
				
				# 평단가가 있으면 평단가 기준, 없으면 기준가 기준으로 1차 익절가 체크
				target_price = 0
				if avg_price > 0:
					target_price = avg_price * (1 + sell_steps[0] / 100)
				elif base_price > 0:
					# 기준가 기준으로 1차 익절가 계산 (매수 단계 완료 전에도 전환 가능)
					target_price = base_price * (1 + sell_steps[0] / 100)
				
				if target_price > 0 and current_price >= target_price:
					# SELL 모드로 전환
					status['current_phase'] = 'SELL'
					status['step_index'] = 0
					
					# 평단가가 없으면 현재가를 평단가로 설정 (보유 수량이 있는 경우)
					holding_qty = status.get('holding_qty', 0) or status.get('total_qty', 0)
					if avg_price <= 0 and holding_qty > 0:
						# 실제 계좌에서 평단가 조회
						real_avg_price = await get_holding_avg_price(code, self.token)
						if real_avg_price > 0:
							status['avg_price'] = real_avg_price
					
					status['monitoring'] = {
						'lowest_price': 0,
						'highest_price': current_price
					}
					price_basis = f"평단가 {avg_price:,.0f}원" if avg_price > 0 else f"기준가 {base_price:,.0f}원"
					await tel_send(f"🌊 {status.get('name', code)} 1차 익절가 도달 ({price_basis} 기준 +{sell_steps[0]}%). 매도 모드로 전환합니다.")
					save_wave_status(status_data)
			
		except Exception as e:
			print(f"종목 처리 오류 ({code}): {e}")
	
	async def _process_buy_phase(self, code, status, status_data, current_price):
		"""매수 단계를 처리합니다."""
		config = load_wave_config()
		buy_steps = config.get('buy_steps', [5.0, 10.0, 3.0])
		buy_ratios = config.get('buy_ratios', [0.33, 0.33, 0.34])
		base_price = status.get('base_price', 0)
		step_index = status.get('step_index', 0)
		total_invest = status.get('total_invest', 0)  # 사용자가 설정한 총 투자금액
		
		# 매수 비활성화 체크: step_index에 해당하는 buy_steps 값이 0이면 매수 로직을 비활성화
		if step_index < len(buy_steps) and buy_steps[step_index] == 0:
			return
		
		if base_price <= 0:
			return
		
		if total_invest <= 0:
			await tel_send(f"❌ {status.get('name', code)} 총 투자금액이 설정되지 않았습니다.")
			return
		
		# Step 0: 1차 매수
		if step_index == 0:
			target_price = base_price * (1 - buy_steps[0] / 100)
			if current_price <= target_price:
				# 매수 실행 (지정가)
				invest_amount = total_invest * buy_ratios[0]
				qty = int(invest_amount / current_price)
				if qty <= 0:
					return
				
				return_code, order_no = await buy_stock(code, qty, current_price, 'N', '', self.token, skip_timeout=True, order_type='limit')
				if return_code == 0:
					# 매수 성공
					status['step_index'] = 1
					status['total_qty'] = status.get('total_qty', 0) + qty
					status['accumulated_invest'] = status.get('accumulated_invest', 0) + (qty * current_price)
					status['avg_price'] = status['accumulated_invest'] / status['total_qty']
					await tel_send(f"🌊 {status.get('name', code)} 1차 매수 주문. {qty}주 @ {current_price:,.0f}원")
					save_wave_status(status_data)
		
		# Step 1: 2차 매수
		elif step_index == 1:
			target_price = base_price * (1 - buy_steps[1] / 100)
			if current_price <= target_price:
				# 매수 실행 (지정가)
				invest_amount = total_invest * buy_ratios[1]
				qty = int(invest_amount / current_price)
				if qty <= 0:
					return
				
				return_code, order_no = await buy_stock(code, qty, current_price, 'N', '', self.token, skip_timeout=True, order_type='limit')
				if return_code == 0:
					# 매수 성공
					status['step_index'] = 2
					status['total_qty'] = status.get('total_qty', 0) + qty
					status['accumulated_invest'] = status.get('accumulated_invest', 0) + (qty * current_price)
					status['avg_price'] = status['accumulated_invest'] / status['total_qty']
					status['monitoring'] = {
						'lowest_price': current_price,
						'highest_price': 0
					}
					await tel_send(f"🌊 {status.get('name', code)} 2차 매수 주문. {qty}주 @ {current_price:,.0f}원")
					save_wave_status(status_data)
		
		# Step 2: 3차 매수 (트레일링)
		elif step_index == 2:
			monitoring = status.get('monitoring', {})
			lowest_price = monitoring.get('lowest_price', current_price)
			
			# 최저가 업데이트 및 반등 체크
			price_updated = False
			if current_price < lowest_price or lowest_price == 0:
				old_lowest = lowest_price
				lowest_price = current_price
				monitoring['lowest_price'] = lowest_price
				status['monitoring'] = monitoring
				price_updated = True
				
				# 최저가 갱신 로그
				if old_lowest > 0:
					print(f"[Wave 3차 매수] {status.get('name', code)} 최저가 갱신: {old_lowest:,.0f}원 → {lowest_price:,.0f}원")
				else:
					print(f"[Wave 3차 매수] {status.get('name', code)} 최저가 설정: {lowest_price:,.0f}원")
				
				# 최저가 갱신 후 즉시 반등 체크 (갱신과 동시에 반등 조건 확인)
				target_price = lowest_price * (1 + buy_steps[2] / 100)
				if current_price >= target_price:
					print(f"[Wave 3차 매수] {status.get('name', code)} 반등 조건 충족: 현재가 {current_price:,.0f}원 >= 목표가 {target_price:,.0f}원 (최저가 {lowest_price:,.0f}원 +{buy_steps[2]}%)")
					# 반등 조건 충족 시 바로 매수 진행 (아래 코드로 계속)
				else:
					# 반등 조건 미충족 시 저장 후 다음 루프에서 재확인
					save_wave_status(status_data)
					return
			
			# 반등 체크 (최저가 갱신이 없었거나 이미 갱신 후 반등 조건을 확인한 경우)
			if not price_updated:
				target_price = lowest_price * (1 + buy_steps[2] / 100)
				if current_price >= target_price:
					print(f"[Wave 3차 매수] {status.get('name', code)} 반등 조건 충족: 현재가 {current_price:,.0f}원 >= 목표가 {target_price:,.0f}원 (최저가 {lowest_price:,.0f}원 +{buy_steps[2]}%)")
				else:
					# 반등 조건 미충족 - 로그 출력 (너무 자주 출력하지 않도록)
					last_log_time = monitoring.get('last_log_time', 0)
					current_time = time.time()
					if current_time - last_log_time > 10:  # 10초마다 한 번씩만 로그
						print(f"[Wave 3차 매수] {status.get('name', code)} 반등 대기: 현재가 {current_price:,.0f}원 < 목표가 {target_price:,.0f}원 (최저가 {lowest_price:,.0f}원 +{buy_steps[2]}%)")
						monitoring['last_log_time'] = current_time
						status['monitoring'] = monitoring
						save_wave_status(status_data)
					return
			
			# 반등 조건 충족 - 매수 실행
			if current_price >= target_price:
				# 매수 실행
				invest_amount = total_invest * buy_ratios[2]
				qty = int(invest_amount / current_price)
				if qty <= 0:
					return
				
				return_code, order_no = await buy_stock(code, qty, 0, 'N', '', self.token, skip_timeout=True, order_type='market')
				if return_code == 0:
					# 매수 성공 - SELL 모드로 전환
					status['current_phase'] = 'SELL'
					status['step_index'] = 0
					status['total_qty'] = status.get('total_qty', 0) + qty
					status['accumulated_invest'] = status.get('accumulated_invest', 0) + (qty * current_price)
					status['avg_price'] = status['accumulated_invest'] / status['total_qty']
					status['monitoring'] = {
						'lowest_price': 0,
						'highest_price': current_price
					}
					await tel_send(f"🌊 {status.get('name', code)} 3차 매수 주문. {qty}주 @ {current_price:,.0f}원. 매도 모드로 전환합니다.")
					save_wave_status(status_data)
	
	async def _process_sell_phase(self, code, status, status_data, current_price):
		"""매도 단계를 처리합니다."""
		config = load_wave_config()
		sell_steps = config.get('sell_steps', [5.0, 10.0, 3.0])
		sell_ratios = config.get('sell_ratios', [0.33, 0.33, 0.34])
		avg_price = status.get('avg_price', 0)
		step_index = status.get('step_index', 0)
		
		# 매도 비활성화 체크: step_index에 해당하는 sell_steps 값이 0이면 매도 로직을 비활성화
		if step_index < len(sell_steps) and sell_steps[step_index] == 0:
			return
		
		if avg_price <= 0:
			return
		
		# 보유 수량 확인 (실제 계좌 데이터 사용)
		holding_qty = await get_holding_quantity(code, self.token)
		if holding_qty <= 0:
			# 보유 수량이 없으면 종료
			print(f"[Wave 매도] {status.get('name', code)} 보유 수량이 0입니다. 분할 트레이딩 종료.")
			if code in status_data:
				del status_data[code]
				save_wave_status(status_data)
			return
		
		# 실제 계좌 데이터와 동기화된 평단가 사용
		real_avg_price = await get_holding_avg_price(code, self.token)
		if real_avg_price > 0 and abs(real_avg_price - avg_price) > 0.01:
			print(f"[Wave 매도] {status.get('name', code)} 평단가 동기화: {avg_price:,.0f}원 → {real_avg_price:,.0f}원")
			avg_price = real_avg_price
			status['avg_price'] = real_avg_price
			save_wave_status(status_data)
		
		# Step 0: 1차 매도
		if step_index == 0:
			target_price = avg_price * (1 + sell_steps[0] / 100)
			if current_price >= target_price:
				print(f"[Wave 1차 매도] {status.get('name', code)} 조건 충족: 현재가 {current_price:,.0f}원 >= 목표가 {target_price:,.0f}원 (평단가 {avg_price:,.0f}원 +{sell_steps[0]}%)")
				# 매도 실행 (33%) - 지정가
				sell_qty = int(holding_qty * sell_ratios[0])
				if sell_qty <= 0:
					sell_qty = 1  # 최소 1주
				
				return_code, _ = await sell_stock(code, sell_qty, 'N', '', self.token, price=target_price, order_type='limit')
				if return_code == 0:
					# 매도 성공
					status['step_index'] = 1
					status['monitoring'] = {
						'lowest_price': 0,
						'highest_price': current_price
					}
					await tel_send(f"🌊 {status.get('name', code)} 1차 매도 주문. {sell_qty}주 @ {current_price:,.0f}원")
					save_wave_status(status_data)
				else:
					print(f"[Wave 1차 매도] {status.get('name', code)} 매도 주문 실패 (return_code: {return_code})")
			else:
				# 조건 불충족 로그 (너무 자주 출력하지 않도록)
				monitoring = status.get('monitoring', {})
				last_log_time = monitoring.get('last_log_time', 0)
				current_time = time.time()
				if current_time - last_log_time > 10:  # 10초마다 한 번씩만 로그
					print(f"[Wave 1차 매도] {status.get('name', code)} 조건 미충족: 현재가 {current_price:,.0f}원 < 목표가 {target_price:,.0f}원 (평단가 {avg_price:,.0f}원 +{sell_steps[0]}%)")
					monitoring['last_log_time'] = current_time
					status['monitoring'] = monitoring
					save_wave_status(status_data)
		
		# Step 1: 2차 매도
		elif step_index == 1:
			target_price = avg_price * (1 + sell_steps[1] / 100)
			if current_price >= target_price:
				print(f"[Wave 2차 매도] {status.get('name', code)} 조건 충족: 현재가 {current_price:,.0f}원 >= 목표가 {target_price:,.0f}원 (평단가 {avg_price:,.0f}원 +{sell_steps[1]}%)")
				# 매도 실행 (33%) - 지정가
				holding_qty = await get_holding_quantity(code, self.token)
				if holding_qty <= 0:
					return
				
				sell_qty = int(holding_qty * sell_ratios[1])
				if sell_qty <= 0:
					sell_qty = 1
				
				return_code, _ = await sell_stock(code, sell_qty, 'N', '', self.token, price=target_price, order_type='limit')
				if return_code == 0:
					# 매도 성공
					status['step_index'] = 2
					monitoring = status.get('monitoring', {})
					monitoring['highest_price'] = current_price
					status['monitoring'] = monitoring
					await tel_send(f"🌊 {status.get('name', code)} 2차 매도 주문. {sell_qty}주 @ {current_price:,.0f}원")
					save_wave_status(status_data)
				else:
					print(f"[Wave 2차 매도] {status.get('name', code)} 매도 주문 실패 (return_code: {return_code})")
			else:
				# 조건 불충족 로그 (너무 자주 출력하지 않도록)
				monitoring = status.get('monitoring', {})
				last_log_time = monitoring.get('last_log_time', 0)
				current_time = time.time()
				if current_time - last_log_time > 10:  # 10초마다 한 번씩만 로그
					print(f"[Wave 2차 매도] {status.get('name', code)} 조건 미충족: 현재가 {current_price:,.0f}원 < 목표가 {target_price:,.0f}원 (평단가 {avg_price:,.0f}원 +{sell_steps[1]}%)")
					monitoring['last_log_time'] = current_time
					status['monitoring'] = monitoring
					save_wave_status(status_data)
		
		# Step 2: 3차 매도 (트레일링)
		elif step_index == 2:
			monitoring = status.get('monitoring', {})
			highest_price = monitoring.get('highest_price', current_price)
			
			# 최고가 업데이트
			if current_price > highest_price:
				old_highest = highest_price
				highest_price = current_price
				monitoring['highest_price'] = highest_price
				status['monitoring'] = monitoring
				print(f"[Wave 3차 매도] {status.get('name', code)} 최고가 갱신: {old_highest:,.0f}원 → {highest_price:,.0f}원")
				save_wave_status(status_data)
				return
			
			# 하락 체크
			target_price = highest_price * (1 - sell_steps[2] / 100)
			if current_price <= target_price:
				print(f"[Wave 3차 매도] {status.get('name', code)} 하락 조건 충족: 현재가 {current_price:,.0f}원 <= 목표가 {target_price:,.0f}원 (최고가 {highest_price:,.0f}원 -{sell_steps[2]}%)")
				# 전량 매도 - 시장가
				holding_qty = await get_holding_quantity(code, self.token)
				if holding_qty <= 0:
					print(f"[Wave 3차 매도] {status.get('name', code)} 보유 수량이 0입니다. 종료.")
					if code in status_data:
						del status_data[code]
						save_wave_status(status_data)
					return
				
				return_code, _ = await sell_stock(code, holding_qty, 'N', '', self.token, order_type='market')
				if return_code == 0:
					# 매도 성공 - 종료
					if code in status_data:
						del status_data[code]
						save_wave_status(status_data)
					await tel_send(f"🌊 {status.get('name', code)} 3차 매도 주문. {holding_qty}주 @ {current_price:,.0f}원. 분할 트레이딩 종료.")
					return
				else:
					print(f"[Wave 3차 매도] {status.get('name', code)} 매도 주문 실패 (return_code: {return_code})")
			else:
				# 하락 조건 미충족 - 로그 출력 (너무 자주 출력하지 않도록)
				last_log_time = monitoring.get('last_log_time', 0)
				current_time = time.time()
				if current_time - last_log_time > 10:  # 10초마다 한 번씩만 로그
					print(f"[Wave 3차 매도] {status.get('name', code)} 하락 대기: 현재가 {current_price:,.0f}원 > 목표가 {target_price:,.0f}원 (최고가 {highest_price:,.0f}원 -{sell_steps[2]}%)")
					monitoring['last_log_time'] = current_time
					status['monitoring'] = monitoring
					save_wave_status(status_data)

