import sys
import os
import json

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.get_setting import cached_setting
from utils.stock_code_normalizer import normalize_stock_code

def _get_grid_status_path():
	"""grid_status.json 파일 경로를 반환합니다. (config/data 폴더 내부)"""
	# 현재 파일 위치: automation/utils/blocklist_checker.py
	# automation 폴더로 이동
	utils_dir = os.path.dirname(os.path.abspath(__file__))
	automation_dir = os.path.dirname(utils_dir)
	# 상위 디렉토리로 이동 (stock-member)
	base_dir = os.path.dirname(automation_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	# data 폴더가 없으면 생성
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'grid_status.json')

def _get_wave_status_path():
	"""wave_status.json 파일 경로를 반환합니다. (config/data 폴더 내부)"""
	# 현재 파일 위치: automation/utils/blocklist_checker.py
	# automation 폴더로 이동
	utils_dir = os.path.dirname(os.path.abspath(__file__))
	automation_dir = os.path.dirname(utils_dir)
	# 상위 디렉토리로 이동 (stock-member)
	base_dir = os.path.dirname(automation_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	# data 폴더가 없으면 생성
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'wave_status.json')

def is_in_grid_trading(stk_cd):
	"""
	종목코드가 그리드 트레이딩에 등록되어 있는지 확인합니다.
	
	Args:
		stk_cd: 종목코드 (예: "005930")
	
	Returns:
		bool: 그리드 트레이딩에 등록되어 있으면 True, 없으면 False
	"""
	try:
		file_path = _get_grid_status_path()
		if not os.path.exists(file_path):
			return False
		
		with open(file_path, 'r', encoding='utf-8') as f:
			grid_data = json.load(f)
		
		# 종목코드 정규화하여 비교
		stk_cd_clean = normalize_stock_code(stk_cd)
		
		# 그리드 트레이딩에 등록된 종목인지 확인
		return stk_cd_clean in grid_data
	except Exception as e:
		print(f"그리드 트레이딩 확인 중 오류: {e}")
		return False

def is_in_wave_trading(stk_cd):
	"""
	종목코드가 분할 트레이딩(기능 8)에 등록되어 있는지 확인합니다.
	
	Args:
		stk_cd: 종목코드 (예: "005930")
	
	Returns:
		bool: 분할 트레이딩에 등록되어 있으면 True, 없으면 False
	"""
	try:
		file_path = _get_wave_status_path()
		if not os.path.exists(file_path):
			return False
		
		with open(file_path, 'r', encoding='utf-8') as f:
			wave_data = json.load(f)
		
		# 종목코드 정규화하여 비교
		stk_cd_clean = normalize_stock_code(stk_cd)
		
		# 분할 트레이딩에 등록된 종목인지 확인
		return stk_cd_clean in wave_data
	except Exception as e:
		print(f"분할 트레이딩 확인 중 오류: {e}")
		return False

def is_blocked(stk_cd):
	"""
	종목코드가 자동매도 금지 목록에 있는지 확인합니다.
	
	Args:
		stk_cd: 종목코드 (예: "005930")
	
	Returns:
		bool: 금지 목록에 있으면 True, 없으면 False
	"""
	try:
		blocklist = cached_setting('auto_sell_blocklist', [])
		
		# blocklist가 리스트가 아니면 빈 리스트로 처리
		if not isinstance(blocklist, list):
			return False
		
		# 종목코드가 금지 목록의 어떤 항목을 포함하는지 확인
		for pattern in blocklist:
			if pattern and pattern in stk_cd:
				return True
		
		return False
	except Exception as e:
		print(f"금지 목록 확인 중 오류: {e}")
		return False

