import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.check_bid import fn_ka10004
from api.buy_stock import fn_kt10000
from api.sell_stock import fn_kt10001
from telegram.tel_send import tel_send
from api.stock_info import fn_ka10001

def get_grid_status_path(script_dir):
	"""grid_status.json 파일 경로를 반환합니다. (config/data 폴더 내부)"""
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	# data 폴더가 없으면 생성
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'grid_status.json')

def load_grid_status(script_dir):
	"""grid_status.json 파일을 로드합니다."""
	file_path = get_grid_status_path(script_dir)
	if os.path.exists(file_path):
		try:
			with open(file_path, 'r', encoding='utf-8') as f:
				return json.load(f)
		except Exception as e:
			print(f"grid_status.json 로드 실패: {e}")
			return {}
	return {}

def save_grid_status(script_dir, data):
	"""grid_status.json 파일에 저장합니다."""
	file_path = get_grid_status_path(script_dir)
	try:
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, indent=2, ensure_ascii=False)
		return True
	except Exception as e:
		print(f"grid_status.json 저장 실패: {e}")
		return False

async def check_and_trade(script_dir, stock_code, token):
	"""
	그리드 트레이딩 로직을 실행합니다.
	
	Args:
		script_dir: 스크립트 디렉토리 경로
		stock_code: 종목코드
		token: API 토큰
	
	Returns:
		True: 정상 처리, False: 오류 발생
	"""
	try:
		# grid_status.json 로드
		grid_data = load_grid_status(script_dir)
		
		if stock_code not in grid_data:
			# 등록되지 않은 종목이면 무시
			return True
		
		config = grid_data[stock_code]
		base_price = float(config.get('base_price', 0))
		max_steps = int(config.get('max_steps', 1))
		price_gap = float(config.get('price_gap', 0))
		order_amount = int(config.get('order_amount', 0))
		current_step = int(config.get('current_step', 1))
		qty_per_step = config.get('qty_per_step', {})
		last_price = config.get('last_price', 0)  # 이전 가격 추적
		check_count = config.get('check_count', 0)  # 체크 횟수 추적
		
		if base_price <= 0 or price_gap <= 0 or order_amount <= 0:
			print(f"그리드 설정 오류: 종목 {stock_code}")
			return False
		
		# 현재가 조회 (silent 모드로 출력 제어)
		current_price = await fn_ka10004(stock_code, token=token, silent=True)
		if current_price <= 0:
			print(f"현재가 조회 실패: 종목 {stock_code}")
			return False
		
		# 종목명 조회 (silent 모드로 출력 제어)
		stock_info_data = await fn_ka10001(stock_code, token=token, silent=True)
		stock_name = stock_info_data.get('stk_nm', stock_code) if isinstance(stock_info_data, dict) else stock_code
		
		# 체크 횟수 증가
		check_count += 1
		should_print = False
		
		# 가격이 변경되었거나 10번에 한 번은 출력
		if last_price != current_price:
			should_print = True
			# 마지막 가격 업데이트
			grid_data[stock_code]['last_price'] = current_price
			check_count = 0  # 가격 변경 시 카운터 리셋
		elif check_count >= 10:
			should_print = True
			check_count = 0  # 10번째 체크 시 카운터 리셋
		
		if should_print:
			print(f"{stock_name}({stock_code}) {current_price:,.0f}원")
		
		# 체크 횟수는 항상 저장 (다음 체크를 위해 파일에 저장)
		grid_data[stock_code]['check_count'] = check_count
		# 상태 저장 (항상 저장하여 check_count 유지)
		save_grid_status(script_dir, grid_data)
		
		# 매수 로직: 하락 시 물타기
		# 1단계 매수 조건: current_step == 0이고 current_price <= base_price - price_gap
		# 2단계 이상 매수 조건: current_price <= base_price - ((current_step + 1) * price_gap)
		#   - current_step만큼 이미 보유 중이므로, 다음 매수는 current_step + 1 단계 가격에서 이루어져야 함
		# 추가 조건: current_step < max_steps
		if current_step == 0:
			# 1단계 매수: 기준가에서 price_gap만큼 떨어졌을 때
			buy_trigger_price = base_price - price_gap
		else:
			# 2단계 이상 매수: 기준가에서 ((current_step + 1) * price_gap)만큼 떨어졌을 때
			# current_step만큼 이미 보유 중이므로, 다음 매수는 current_step + 1 단계 가격에서 이루어져야 함
			buy_trigger_price = base_price - ((current_step + 1) * price_gap)
		
		if current_price <= buy_trigger_price and current_step < max_steps:
			# 매수 실행
			qty = int(order_amount / current_price)
			if qty < 1:
				print(f"매수 수량 계산 오류: 종목 {stock_code}, 현재가: {current_price}, 금액: {order_amount}")
				return False
			
			# 시장가 매수 주문
			return_code, order_no = await fn_kt10000(
				stk_cd=stock_code,
				ord_qty=qty,
				ord_uv="0",  # 시장가
				token=token,
				skip_timeout=True,  # 그리드 트레이딩은 타임아웃 모니터링 건너뛰기
				order_type='market'  # 설정 파일과 무관하게 시장가 주문 강제
			)
			
			if return_code == 0:
				# 상태 업데이트
				new_step = current_step + 1
				grid_data[stock_code]['current_step'] = new_step
				if 'qty_per_step' not in grid_data[stock_code]:
					grid_data[stock_code]['qty_per_step'] = {}
				grid_data[stock_code]['qty_per_step'][str(new_step)] = qty
				
				if save_grid_status(script_dir, grid_data):
					# 알림 전송
					step_label = "1단계" if new_step == 1 else f"{new_step}단계"
					message = f"📈 [그리드 {step_label} 매수] {stock_name}({stock_code})\n"
					message += f"{step_label} 매수: {qty}주 @ {current_price:,.0f}원\n"
					message += f"현재 단계: {new_step}/{max_steps}"
					await tel_send(message)
					return True
			else:
				print(f"그리드 매수 주문 실패: 종목 {stock_code}, 코드: {return_code}")
				return False
		
		# 매도 로직: 반등 시 차익 실현
		# 조건: current_price >= base_price - ((current_step - 2) * price_gap)
		# 필수 조건: current_step > 1 (1단계 물량은 절대 매도하지 않음)
		if current_step > 1:
			sell_trigger_price = base_price - ((current_step - 2) * price_gap)
			
			if current_price >= sell_trigger_price:
				# 매도 실행
				# 해당 단계에서 매수했던 수량만큼 매도
				step_qty = qty_per_step.get(str(current_step), 0)
				if step_qty == 0:
					# qty_per_step에 기록이 없으면 근사치 계산
					step_qty = int(order_amount / current_price)
				
				if step_qty < 1:
					print(f"매도 수량 계산 오류: 종목 {stock_code}, 단계: {current_step}")
					return False
				
				# 시장가 매도 주문
				return_code, _ = await fn_kt10001(
					stk_cd=stock_code,
					ord_qty=step_qty,
					token=token
				)
				
				if return_code == 0:
					# 상태 업데이트
					new_step = current_step - 1
					grid_data[stock_code]['current_step'] = new_step
					# qty_per_step에서 해당 단계 제거
					if 'qty_per_step' in grid_data[stock_code] and str(current_step) in grid_data[stock_code]['qty_per_step']:
						del grid_data[stock_code]['qty_per_step'][str(current_step)]
					
					if save_grid_status(script_dir, grid_data):
						# 알림 전송
						message = f"📉 [그리드 매도] {stock_name}({stock_code})\n"
						message += f"{current_step}단계 매도: {step_qty}주 @ {current_price:,.0f}원\n"
						message += f"현재 단계: {new_step}/{max_steps}"
						await tel_send(message)
						return True
				else:
					print(f"그리드 매도 주문 실패: 종목 {stock_code}, 코드: {return_code}")
					return False
		
		# 매수/매도 조건이 충족되지 않음
		return True
		
	except Exception as e:
		print(f"그리드 트레이딩 로직 실행 중 오류: 종목 {stock_code}, {e}")
		return False

