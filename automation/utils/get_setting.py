import os
import time
import json
import shutil

def get_setting(key, default=''):
	try:
		script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		base_dir = os.path.dirname(script_dir)
		config_dir = os.path.join(base_dir, 'config')
		data_dir = os.path.join(config_dir, 'data')
		settings_path = os.path.join(data_dir, 'settings.json')
		settings_default_path = os.path.join(script_dir, 'settings-default.json')
		
		# data 폴더가 없으면 생성
		if not os.path.exists(data_dir):
			os.makedirs(data_dir, exist_ok=True)
		
		# settings.json이 없으면 settings-default.json을 복사
		if not os.path.exists(settings_path):
			if os.path.exists(settings_default_path):
				shutil.copy2(settings_default_path, settings_path)
				print(f"settings.json이 없어서 settings-default.json을 복사했습니다.")
			else:
				print(f"경고: settings-default.json 파일도 없습니다.")
		
		with open(settings_path, 'r', encoding='utf-8') as f:
			settings = json.load(f)
		return settings.get(key, default)
	except Exception as e:
		print(f"오류 발생(get_setting): {e}")
		return default

def cached_setting(key, default=''):
	# 여러 key 값의 캐시 관리 (value, read_time) 형태로 저장
	if not hasattr(cached_setting, "_cache"):
		cached_setting._cache = {}

	now = time.time()
	cache = cached_setting._cache

	value_info = cache.get(key, (None, 0))
	cached_value, last_read_time = value_info

	if now - last_read_time > 10 or cached_value is None:
		# 10초 경과하거나 캐시 없음 → 새로 읽음
		cached_value = get_setting(key, default)
		cache[key] = (cached_value, now)
	return cached_value

