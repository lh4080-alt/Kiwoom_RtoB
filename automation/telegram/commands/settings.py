import json
import os
import sys

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

class SettingsManager:
	"""설정 파일 관리를 담당하는 클래스"""
	
	def __init__(self, script_dir):
		self.script_dir = script_dir
		base_dir = os.path.dirname(script_dir)
		config_dir = os.path.join(base_dir, 'config')
		data_dir = os.path.join(config_dir, 'data')
		# data 폴더가 없으면 생성
		if not os.path.exists(data_dir):
			os.makedirs(data_dir, exist_ok=True)
		self.settings_path = os.path.join(data_dir, 'settings.json')
	
	def get_setting(self, key, default=None):
		"""settings.json 파일에서 특정 키 값을 가져옵니다."""
		try:
			with open(self.settings_path, 'r', encoding='utf-8') as f:
				settings = json.load(f)
			return settings.get(key, default)
		except Exception as e:
			print(f"설정 읽기 실패: {e}")
			return default
	
	def update_setting(self, key, value):
		"""settings.json 파일의 특정 키 값을 업데이트합니다."""
		try:
			with open(self.settings_path, 'r', encoding='utf-8') as f:
				settings = json.load(f)
			
			settings[key] = value
			
			with open(self.settings_path, 'w', encoding='utf-8') as f:
				json.dump(settings, f, ensure_ascii=False, indent=2)
			
			return True
		except Exception as e:
			print(f"설정 업데이트 실패: {e}")
			return False

