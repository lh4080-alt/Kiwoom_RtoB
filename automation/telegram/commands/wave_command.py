import json
import os
import sys

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.stock_info import get_current_price, fn_ka10001
from telegram.tel_send import tel_send

def get_wave_config_path(script_dir):
	"""wave_config.json 파일 경로를 반환합니다."""
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'wave_config.json')

def get_wave_status_path(script_dir):
	"""wave_status.json 파일 경로를 반환합니다."""
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'wave_status.json')

def load_wave_config(script_dir):
	"""wave_config.json 파일을 로드합니다."""
	file_path = get_wave_config_path(script_dir)
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

def save_wave_config(script_dir, data):
	"""wave_config.json 파일에 저장합니다."""
	file_path = get_wave_config_path(script_dir)
	try:
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, indent=2, ensure_ascii=False)
		return True
	except Exception as e:
		print(f"wave_config.json 저장 실패: {e}")
		return False

def load_wave_status(script_dir):
	"""wave_status.json 파일을 로드합니다."""
	file_path = get_wave_status_path(script_dir)
	if os.path.exists(file_path):
		try:
			with open(file_path, 'r', encoding='utf-8') as f:
				return json.load(f)
		except Exception as e:
			print(f"wave_status.json 로드 실패: {e}")
			return {}
	return {}

def save_wave_status(script_dir, data):
	"""wave_status.json 파일에 저장합니다."""
	file_path = get_wave_status_path(script_dir)
	try:
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, indent=2, ensure_ascii=False)
		return True
	except Exception as e:
		print(f"wave_status.json 저장 실패: {e}")
		return False

async def process_wave_set(script_dir, args):
	"""
	wave set 명령어를 처리합니다.
	
	사용법: wave set buy <n> <n> <n> sell <n> <n> <n>
	예: wave set buy 5 10 3 sell 5 10 3
	"""
	try:
		if len(args) != 8:  # buy n n n sell n n n = 8개
			await tel_send("❌ 사용법: wave set buy <n> <n> <n> sell <n> <n> <n>\n예: wave set buy 5 10 3 sell 5 10 3")
			return False
		
		if args[0] != 'buy' or args[4] != 'sell':
			await tel_send("❌ 사용법: wave set buy <n> <n> <n> sell <n> <n> <n>\n예: wave set buy 5 10 3 sell 5 10 3")
			return False
		
		try:
			buy_steps = [float(args[1]), float(args[2]), float(args[3])]
			sell_steps = [float(args[5]), float(args[6]), float(args[7])]
		except (ValueError, IndexError):
			await tel_send("❌ 모든 값은 숫자여야 합니다.")
			return False
		
		# 기존 설정 로드
		config = load_wave_config(script_dir)
		config['buy_steps'] = buy_steps
		config['sell_steps'] = sell_steps
		
		if not save_wave_config(script_dir, config):
			await tel_send("❌ 설정 저장에 실패했습니다.")
			return False
		
		message = f"✅ 분할 트레이딩 설정이 저장되었습니다.\n\n"
		
		# 매수 단계 메시지 생성
		if buy_steps[0] == 0 or all(step == 0 for step in buy_steps):
			message += f"⛔ 매수 로직: 비활성화 (모든 단계 Skip)\n"
		else:
			message += f"매수 단계: {buy_steps[0]}%, {buy_steps[1]}%, {buy_steps[2]}%\n"
		
		# 매도 단계 메시지 생성
		if sell_steps[0] == 0 or all(step == 0 for step in sell_steps):
			message += f"⛔ 매도 로직: 비활성화 (보유 시에도 매도 안 함)"
		else:
			message += f"매도 단계: {sell_steps[0]}%, {sell_steps[1]}%, {sell_steps[2]}%"
		
		await tel_send(message)
		return True
		
	except Exception as e:
		await tel_send(f"❌ wave set 명령어 실행 중 오류: {e}")
		return False

async def process_wave_add(script_dir, token_manager, args):
	"""
	wave add 명령어를 처리합니다.
	
	사용법: wave add <종목코드> <총금액> [기준가]
	예: wave add 005930 300000 70000
	"""
	try:
		if len(args) < 2 or len(args) > 3:
			await tel_send("❌ 사용법: wave add <종목코드> <총금액> [기준가]\n예: wave add 005930 300000 70000")
			return False
		
		code = args[0].strip()
		try:
			total_amount = int(args[1])
		except ValueError:
			await tel_send("❌ 총금액은 숫자여야 합니다.")
			return False
		
		if total_amount <= 0:
			await tel_send("❌ 총금액은 0보다 커야 합니다.")
			return False
		
		# 토큰 가져오기
		token = await token_manager.get_token()
		if not token:
			await tel_send("❌ 토큰 발급에 실패했습니다.")
			return False
		
		# 기준가 설정
		if len(args) == 3:
			try:
				base_price = float(args[2])
			except ValueError:
				await tel_send("❌ 기준가는 숫자여야 합니다.")
				return False
		else:
			# 기준가 미입력 시 현재가 조회
			base_price = await get_current_price(code, token)
			if base_price <= 0:
				await tel_send(f"❌ 종목 {code}의 현재가 조회에 실패했습니다.")
				return False
		
		# 종목명 조회
		stock_info_data = await fn_ka10001(code, token=token, silent=True)
		stock_name = stock_info_data.get('stk_nm', code) if isinstance(stock_info_data, dict) else code
		
		# wave_status.json 로드
		status_data = load_wave_status(script_dir)
		
		# 이미 등록된 종목인지 확인
		if code in status_data:
			await tel_send(f"❌ 종목 {stock_name}({code})는 이미 분할 트레이딩에 등록되어 있습니다.\n제거하려면 'wave remove {code}' 명령을 사용하세요.")
			return False
		
		# 종목 추가
		status_data[code] = {
			"code": code,
			"name": stock_name,
			"base_price": base_price,
			"current_phase": "BUY",
			"step_index": 0,
			"total_qty": 0,
			"avg_price": 0,
			"accumulated_invest": 0,
			"total_invest": total_amount,  # 사용자가 설정한 총 투자금액
			"monitoring": {
				"lowest_price": 0,
				"highest_price": 0
			}
		}
		
		if not save_wave_status(script_dir, status_data):
			await tel_send("❌ 분할 트레이딩 설정 저장에 실패했습니다.")
			return False
		
		# 목표가 계산
		config = load_wave_config(script_dir)
		buy_steps = config.get('buy_steps', [5.0, 10.0, 3.0])
		target_price = base_price * (1 - buy_steps[0] / 100)
		
		# 성공 알림
		message = f"🌊 {stock_name} 등록 완료.\n"
		message += f"기준가: {base_price:,.0f}원\n"
		message += f"총 투자금액: {total_amount:,}원\n"
		message += f"1차 목표가: {target_price:,.0f}원(-{buy_steps[0]}%)\n\n"
		message += f"💡 'start 8' 명령으로 분할 트레이딩을 시작하세요."
		await tel_send(message)
		
		return True
		
	except Exception as e:
		await tel_send(f"❌ wave add 명령어 실행 중 오류: {e}")
		return False

async def process_wave_list(script_dir, token_manager):
	"""
	wave list 명령어를 처리합니다.
	
	현재 감시 중인 모든 종목을 조회합니다.
	"""
	try:
		status_data = load_wave_status(script_dir)
		
		if not status_data:
			await tel_send("📋 감시 중인 분할 트레이딩 종목이 없습니다.")
			return True
		
		config = load_wave_config(script_dir)
		buy_steps = config.get('buy_steps', [5.0, 10.0, 3.0])
		sell_steps = config.get('sell_steps', [5.0, 10.0, 3.0])
		
		# 토큰 가져오기
		token = await token_manager.get_token()
		if not token:
			await tel_send("❌ 토큰 발급에 실패했습니다.")
			return False
		
		message = "📋 [분할 트레이딩 감시 종목]\n\n"
		
		for code, status in status_data.items():
			stock_name = status.get('name', code)
			phase = status.get('current_phase', 'BUY')
			step_index = status.get('step_index', 0)
			base_price = status.get('base_price', 0)
			avg_price = status.get('avg_price', 0)
			total_qty = status.get('total_qty', 0)
			
			# 현재가 조회
			current_price = await get_current_price(code, token)
			
			message += f"📊 {stock_name} ({code})\n"
			message += f"  현재가: {current_price:,.0f}원\n"
			message += f"  단계: {phase} - {step_index + 1}차\n"
			
			if phase == 'BUY':
				if step_index == 0:
					target_price = base_price * (1 - buy_steps[0] / 100)
					message += f"  다음 목표가: {target_price:,.0f}원 (-{buy_steps[0]}%)\n"
				elif step_index == 1:
					target_price = base_price * (1 - buy_steps[1] / 100)
					message += f"  다음 목표가: {target_price:,.0f}원 (-{buy_steps[1]}%)\n"
				else:  # step_index == 2
					monitoring = status.get('monitoring', {})
					lowest_price = monitoring.get('lowest_price', current_price)
					if lowest_price > 0:
						target_price = lowest_price * (1 + buy_steps[2] / 100)
						message += f"  최저가: {lowest_price:,.0f}원\n"
						message += f"  다음 목표가: {target_price:,.0f}원 (반등 {buy_steps[2]}%)\n"
					else:
						message += f"  최저가 감시 중...\n"
			else:  # phase == 'SELL'
				if avg_price > 0:
					if step_index == 0:
						target_price = avg_price * (1 + sell_steps[0] / 100)
						message += f"  평단가: {avg_price:,.0f}원\n"
						message += f"  다음 목표가: {target_price:,.0f}원 (+{sell_steps[0]}%)\n"
					elif step_index == 1:
						target_price = avg_price * (1 + sell_steps[1] / 100)
						message += f"  평단가: {avg_price:,.0f}원\n"
						message += f"  다음 목표가: {target_price:,.0f}원 (+{sell_steps[1]}%)\n"
					else:  # step_index == 2
						monitoring = status.get('monitoring', {})
						highest_price = monitoring.get('highest_price', current_price)
						if highest_price > 0:
							target_price = highest_price * (1 - sell_steps[2] / 100)
							message += f"  평단가: {avg_price:,.0f}원\n"
							message += f"  최고가: {highest_price:,.0f}원\n"
							message += f"  다음 목표가: {target_price:,.0f}원 (하락 {sell_steps[2]}%)\n"
						else:
							message += f"  최고가 감시 중...\n"
				else:
					message += f"  평단가: 계산 중...\n"
			
			if total_qty > 0:
				message += f"  보유수량: {total_qty:,}주\n"
			
			message += "\n"
		
		await tel_send(message)
		return True
		
	except Exception as e:
		await tel_send(f"❌ wave list 명령어 실행 중 오류: {e}")
		return False

async def process_wave_remove(script_dir, token_manager, args):
	"""
	wave remove 명령어를 처리합니다.
	
	사용법: wave remove <종목코드> 또는 wave remove all
	예: wave remove 005930
	예: wave remove all
	"""
	try:
		if len(args) != 1:
			await tel_send("❌ 사용법: wave remove <종목코드> 또는 wave remove all\n예: wave remove 005930\n예: wave remove all")
			return False
		
		code_or_all = args[0].strip()
		
		# wave_status.json 로드
		status_data = load_wave_status(script_dir)
		
		# "all" 옵션 처리
		if code_or_all.lower() == 'all':
			if not status_data:
				await tel_send("📋 제거할 분할 트레이딩 종목이 없습니다.")
				return True
			
			# 토큰 가져오기 (보유 수량 확인용)
			from api.acc_val import fn_kt00004
			from utils.stock_code_normalizer import normalize_stock_code
			
			token = None
			if token_manager:
				try:
					token = await token_manager.get_token()
				except Exception as e:
					print(f"토큰 발급 실패 (wave remove all): {e}")
			
			# 보유 중인 종목은 제외하고 삭제
			removed_codes = []
			kept_codes = []
			
			for code, status in list(status_data.items()):
				holding_qty = status.get('holding_qty', 0) or status.get('total_qty', 0)
				
				# 실제 계좌에서 보유 수량 확인
				if token and holding_qty == 0:
					try:
						my_stocks = await fn_kt00004(False, 'N', '', token)
						if my_stocks:
							for stock in my_stocks:
								stock_code = normalize_stock_code(stock.get('stk_cd', ''))
								if stock_code == code:
									real_qty = int(stock.get('rmnd_qty', '0') or 0)
									if real_qty > 0:
										holding_qty = real_qty
									break
					except Exception as e:
						print(f"보유 수량 확인 오류 ({code}): {e}")
				
				# 보유 수량이 0보다 크면 유지, 0이면 삭제
				if holding_qty > 0:
					kept_codes.append(code)
				else:
					removed_codes.append(code)
					del status_data[code]
			
			if not save_wave_status(script_dir, status_data):
				await tel_send("❌ 분할 트레이딩 설정 저장에 실패했습니다.")
				return False
			
			# 성공 알림
			message = f"✅ 분할 트레이딩 종목 정리 완료.\n"
			message += f"제거된 종목: {len(removed_codes)}개\n"
			if kept_codes:
				kept_names = [status_data.get(code, {}).get('name', code) for code in kept_codes]
				message += f"보유 중인 종목 유지: {len(kept_codes)}개 ({', '.join(kept_names[:5])}"
				if len(kept_codes) > 5:
					message += f" 외 {len(kept_codes) - 5}개"
				message += ")"
			await tel_send(message)
			return True
		
		# 단일 종목 제거
		code = code_or_all
		
		# 종목이 등록되어 있는지 확인
		if code not in status_data:
			await tel_send(f"❌ 종목 {code}는 분할 트레이딩에 등록되어 있지 않습니다.")
			return False
		
		stock_name = status_data[code].get('name', code)
		
		# 종목 제거
		del status_data[code]
		
		if not save_wave_status(script_dir, status_data):
			await tel_send("❌ 분할 트레이딩 설정 저장에 실패했습니다.")
			return False
		
		# 성공 알림
		await tel_send(f"✅ {stock_name}({code})의 분할 트레이딩이 제거되었습니다.")
		return True
		
	except Exception as e:
		await tel_send(f"❌ wave remove 명령어 실행 중 오류: {e}")
		return False

