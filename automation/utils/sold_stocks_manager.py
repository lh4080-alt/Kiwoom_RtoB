"""
매도한 종목 기록 관리 유틸리티
매도 후 n시간 이내 재매수 방지 기능
마지막 보유 시간을 추적하여 다른 프로그램에서 매도한 경우도 감지
"""
import json
import os
import datetime
import sys

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.stock_code_normalizer import normalize_stock_code

def _get_sold_stocks_path():
	"""last_held_stocks.json 파일 경로 반환 (config/data 폴더 내부)"""
	script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	# data 폴더가 없으면 생성
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	# 기존 sold_stocks.json 파일이 있으면 마이그레이션
	old_path = os.path.join(data_dir, 'sold_stocks.json')
	new_path = os.path.join(data_dir, 'last_held_stocks.json')
	
	if os.path.exists(old_path) and not os.path.exists(new_path):
		try:
			import shutil
			shutil.move(old_path, new_path)
			print(f"✓ sold_stocks.json 파일을 last_held_stocks.json으로 이름 변경했습니다.")
		except Exception as e:
			print(f"⚠️ 파일 이름 변경 실패: {e}")
	
	return new_path

def _load_sold_stocks():
	"""last_held_stocks.json 파일에서 마지막 보유 시간 기록을 읽어옵니다."""
	try:
		file_path = _get_sold_stocks_path()
		if not os.path.exists(file_path):
			# 파일이 없으면 빈 객체로 파일 생성
			with open(file_path, 'w', encoding='utf-8') as f:
				json.dump({}, f, ensure_ascii=False, indent=2)
			return {}
		
		with open(file_path, 'r', encoding='utf-8') as f:
			return json.load(f)
	except Exception as e:
		print(f"마지막 보유 시간 기록 읽기 실패: {e}")
		return {}

def _save_sold_stocks(data):
	"""last_held_stocks.json 파일에 마지막 보유 시간 기록을 저장합니다."""
	try:
		file_path = _get_sold_stocks_path()
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, ensure_ascii=False, indent=2)
		return True
	except Exception as e:
		print(f"마지막 보유 시간 기록 저장 실패: {e}")
		return False

def record_sold_stock(stk_cd):
	"""
	매도한 종목을 기록합니다. (하위 호환성을 위해 유지)
	실제로는 update_last_held_time을 사용하는 것이 권장됩니다.
	
	Args:
		stk_cd: 종목 코드 (예: "005930")
	
	Returns:
		bool: 기록 성공 여부
	"""
	# 매도 시점을 기록 (기존 동작 유지)
	return update_last_held_time(stk_cd)

def update_last_held_time(stk_cd):
	"""
	종목의 마지막 보유 시간을 현재 시간으로 업데이트합니다.
	보유 중인 종목에 대해 주기적으로 호출되어야 합니다.
	
	Args:
		stk_cd: 종목 코드 (예: "005930")
	
	Returns:
		bool: 기록 성공 여부
	"""
	try:
		# 종목 코드 정규화 (일관성 유지)
		stk_cd_clean = normalize_stock_code(stk_cd)
		
		# 현재 시각 기록
		now = datetime.datetime.now()
		held_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
		
		# 기존 기록 읽기
		held_stocks = _load_sold_stocks()
		
		# 새 기록 추가 또는 업데이트
		held_stocks[stk_cd_clean] = held_time_str
		
		# 저장
		return _save_sold_stocks(held_stocks)
	except Exception as e:
		print(f"마지막 보유 시간 기록 저장 중 오류: {e}")
		return False

def is_in_cooldown(stk_cd, cooldown_hours):
	"""
	종목이 쿨다운 기간 중인지 확인합니다.
	마지막 보유 시간을 기준으로 쿨다운을 계산합니다.
	
	Args:
		stk_cd: 종목 코드 (예: "005930")
		cooldown_hours: 쿨다운 시간 (시간 단위, 0이면 쿨다운 없음)
	
	Returns:
		bool: 쿨다운 중이면 True, 아니면 False
	"""
	try:
		# 쿨다운이 0이면 항상 False
		if cooldown_hours <= 0:
			return False
		
		# 종목 코드 정규화
		stk_cd_clean = normalize_stock_code(stk_cd)
		
		# 마지막 보유 시간 기록 읽기
		held_stocks = _load_sold_stocks()
		
		# 기록이 없으면 쿨다운 없음
		if stk_cd_clean not in held_stocks:
			return False
		
		# 마지막 보유 시각 파싱
		held_time_str = held_stocks[stk_cd_clean]
		held_time = datetime.datetime.strptime(held_time_str, '%Y-%m-%d %H:%M:%S')
		
		# 현재 시각
		now = datetime.datetime.now()
		
		# 경과 시간 계산
		elapsed = now - held_time
		elapsed_hours = elapsed.total_seconds() / 3600
		
		# 쿨다운 기간 내인지 확인
		return elapsed_hours < cooldown_hours
	except Exception as e:
		print(f"쿨다운 확인 중 오류: {e}")
		# 오류 발생 시 안전하게 False 반환 (매수 가능하도록)
		return False

def get_cooldown_remaining(stk_cd, cooldown_hours):
	"""
	종목의 쿨다운 남은 시간을 반환합니다.
	마지막 보유 시간을 기준으로 계산합니다.
	
	Args:
		stk_cd: 종목 코드 (예: "005930")
		cooldown_hours: 쿨다운 시간 (시간 단위)
	
	Returns:
		float: 남은 시간 (시간 단위), 쿨다운이 없으면 0
	"""
	try:
		# 쿨다운이 0이면 0 반환
		if cooldown_hours <= 0:
			return 0.0
		
		# 종목 코드 정규화
		stk_cd_clean = normalize_stock_code(stk_cd)
		
		# 마지막 보유 시간 기록 읽기
		held_stocks = _load_sold_stocks()
		
		# 기록이 없으면 0 반환
		if stk_cd_clean not in held_stocks:
			return 0.0
		
		# 마지막 보유 시각 파싱
		held_time_str = held_stocks[stk_cd_clean]
		held_time = datetime.datetime.strptime(held_time_str, '%Y-%m-%d %H:%M:%S')
		
		# 현재 시각
		now = datetime.datetime.now()
		
		# 경과 시간 계산
		elapsed = now - held_time
		elapsed_hours = elapsed.total_seconds() / 3600
		
		# 남은 시간 계산
		remaining = cooldown_hours - elapsed_hours
		return max(0.0, remaining)
	except Exception as e:
		print(f"쿨다운 남은 시간 계산 중 오류: {e}")
		return 0.0

def cleanup_expired_records(cooldown_hours):
	"""
	만료된 마지막 보유 시간 기록을 정리합니다.
	
	Args:
		cooldown_hours: 쿨다운 시간 (시간 단위)
	
	Returns:
		bool: 정리 성공 여부
	"""
	try:
		# 쿨다운이 0이면 모든 기록 삭제
		if cooldown_hours <= 0:
			return _save_sold_stocks({})
		
		# 마지막 보유 시간 기록 읽기
		held_stocks = _load_sold_stocks()
		
		# 현재 시각
		now = datetime.datetime.now()
		
		# 만료되지 않은 기록만 남기기
		cleaned_stocks = {}
		for stk_cd, held_time_str in held_stocks.items():
			try:
				held_time = datetime.datetime.strptime(held_time_str, '%Y-%m-%d %H:%M:%S')
				elapsed = now - held_time
				elapsed_hours = elapsed.total_seconds() / 3600
				
				# 쿨다운 기간 내이면 유지
				if elapsed_hours < cooldown_hours:
					cleaned_stocks[stk_cd] = held_time_str
			except Exception as e:
				print(f"기록 정리 중 오류 (종목: {stk_cd}): {e}")
				continue
		
		# 저장
		return _save_sold_stocks(cleaned_stocks)
	except Exception as e:
		print(f"기록 정리 중 오류: {e}")
		return False

