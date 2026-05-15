import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.check_bid import fn_ka10004
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

async def process_grid_add(script_dir, token_manager, args):
	"""
	grid add 명령어를 처리합니다.
	
	사용법: grid add [종목코드] [단계수] [단계별금액차] [매수금액]
	예: grid add 005930 5 1000 100000
	"""
	try:
		# 인자 파싱
		if len(args) != 4:
			await tel_send("❌ 사용법: grid add [종목코드] [단계수] [단계별금액차] [매수금액]\n예: grid add 005930 5 1000 100000")
			return False
		
		stock_code = args[0].strip()
		try:
			max_steps = int(args[1])
			price_gap = int(args[2])
			order_amount = int(args[3])
		except ValueError:
			await tel_send("❌ 단계수, 단계별금액차, 매수금액은 숫자여야 합니다.")
			return False
		
		if max_steps < 1:
			await tel_send("❌ 단계수는 1 이상이어야 합니다.")
			return False
		
		if price_gap <= 0:
			await tel_send("❌ 단계별금액차는 0보다 커야 합니다.")
			return False
		
		if order_amount <= 0:
			await tel_send("❌ 매수금액은 0보다 커야 합니다.")
			return False
		
		# 토큰 가져오기
		token = await token_manager.get_token()
		if not token:
			await tel_send("❌ 토큰 발급에 실패했습니다.")
			return False
		
		# 현재가 조회 (base_price로 사용)
		current_price = await fn_ka10004(stock_code, token=token)
		if current_price <= 0:
			await tel_send(f"❌ 종목 {stock_code}의 현재가 조회에 실패했습니다.")
			return False
		
		# 종목명 조회
		stock_info_data = await fn_ka10001(stock_code, token=token)
		stock_name = stock_info_data.get('stk_nm', stock_code) if isinstance(stock_info_data, dict) else stock_code
		
		# grid_status.json 로드
		grid_data = load_grid_status(script_dir)
		
		# 이미 등록된 종목인지 확인
		if stock_code in grid_data:
			await tel_send(f"❌ 종목 {stock_name}({stock_code})는 이미 그리드 트레이딩에 등록되어 있습니다.\n제거하려면 'grid remove {stock_code}' 명령을 사용하세요.")
			return False
		
		# 그리드 설정 저장 (등록만, 매수는 start 7 후 실행)
		grid_data[stock_code] = {
			"base_price": current_price,
			"max_steps": max_steps,
			"price_gap": price_gap,
			"order_amount": order_amount,
			"current_step": 0,  # 아직 매수하지 않음 (start 7 후 1단계 매수 실행)
			"qty_per_step": {}
		}
		
		if not save_grid_status(script_dir, grid_data):
			await tel_send("❌ 그리드 설정 저장에 실패했습니다.")
			return False
		
		# 성공 알림
		message = f"✅ 그리드 트레이딩 등록 완료\n"
		message += f"종목: {stock_name}({stock_code})\n"
		message += f"기준가: {current_price:,.0f}원\n"
		message += f"최대 단계: {max_steps}단계\n"
		message += f"단계별 가격 차이: {price_gap:,}원\n"
		message += f"1회 매수 금액: {order_amount:,}원\n\n"
		message += f"💡 'start 7' 명령으로 그리드 트레이딩을 시작하세요."
		await tel_send(message)
		
		return True
		
	except Exception as e:
		await tel_send(f"❌ grid add 명령어 실행 중 오류: {e}")
		return False

async def process_grid_remove(script_dir, args):
	"""
	grid remove 명령어를 처리합니다.
	
	사용법: grid remove [종목코드]
	예: grid remove 005930
	"""
	try:
		# 인자 파싱
		if len(args) != 1:
			await tel_send("❌ 사용법: grid remove [종목코드]\n예: grid remove 005930")
			return False
		
		stock_code = args[0].strip()
		
		# grid_status.json 로드
		grid_data = load_grid_status(script_dir)
		
		# 종목이 등록되어 있는지 확인
		if stock_code not in grid_data:
			await tel_send(f"❌ 종목 {stock_code}는 그리드 트레이딩에 등록되어 있지 않습니다.")
			return False
		
		# 종목 제거
		del grid_data[stock_code]
		
		if not save_grid_status(script_dir, grid_data):
			await tel_send("❌ 그리드 설정 저장에 실패했습니다.")
			return False
		
		# 성공 알림
		await tel_send(f"✅ 종목 {stock_code}의 그리드 트레이딩이 제거되었습니다.")
		return True
		
	except Exception as e:
		await tel_send(f"❌ grid remove 명령어 실행 중 오류: {e}")
		return False

async def process_grid_list(script_dir):
	"""
	grid list 명령어를 처리합니다.
	
	현재 등록된 모든 그리드 트레이딩 종목을 조회합니다.
	"""
	try:
		# grid_status.json 로드
		grid_data = load_grid_status(script_dir)
		
		if not grid_data:
			await tel_send("📋 등록된 그리드 트레이딩 종목이 없습니다.")
			return True
		
		# 종목 목록 구성
		message = "📋 [그리드 트레이딩 종목 목록]\n\n"
		for stock_code, config in grid_data.items():
			message += f"종목코드: {stock_code}\n"
			message += f"  기준가: {config.get('base_price', 0):,.0f}원\n"
			message += f"  현재 단계: {config.get('current_step', 1)}/{config.get('max_steps', 1)}\n"
			message += f"  단계별 가격 차이: {config.get('price_gap', 0):,}원\n"
			message += f"  매수 금액: {config.get('order_amount', 0):,}원\n\n"
		
		await tel_send(message)
		return True
		
	except Exception as e:
		await tel_send(f"❌ grid list 명령어 실행 중 오류: {e}")
		return False

