import os

# config 폴더의 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, 'config')

def read_config_file(filename):
	"""config 폴더에서 텍스트 파일을 읽어서 값을 반환"""
	filepath = os.path.join(CONFIG_DIR, filename)
	try:
		with open(filepath, 'r', encoding='utf-8-sig') as f:
			return f.read().strip()
	except FileNotFoundError:
		return ""

# settings.json에서 is_paper_trading 읽기
from utils.get_setting import get_setting

def get_is_paper_trading():
	"""settings.json에서 is_paper_trading 값을 읽어옵니다."""
	value = get_setting('is_paper_trading', True)
	# get_setting이 문자열을 반환할 수 있으므로 boolean으로 변환
	if isinstance(value, str):
		value = value.lower() in ('true', '1', 'yes')
	return bool(value)

# 하위 호환성을 위한 변수 (함수 호출로 값을 가져옴)
def _get_is_paper_trading():
	return get_is_paper_trading()

is_paper_trading = _get_is_paper_trading()

# config 폴더의 텍스트 파일에서 값 읽기
real_app_key = read_config_file('real_app_key.txt')
real_app_secret = read_config_file('real_app_secret.txt')
paper_app_key = read_config_file('paper_app_key.txt')
paper_app_secret = read_config_file('paper_app_secret.txt')

# 텔레그램 — 공유 봇 토큰 (2026-07-16 Lee 지시: @Kwoom_Basic_bot 공유 토큰으로 전환)
# beelink 공용 파일 우선: C:\market_data_collector\config\telegram_*.txt
# (토큰 교체 시 그 파일 한 곳만 수정하면 모든 봇에 반영). 없으면 로컬 config 폴백.
# encoding='utf-8-sig': PowerShell 재저장 시 붙는 BOM 방어 (httpx 헤더 에러 예방).
_SHARED_TG_DIR = r'C:\market_data_collector\config'

def _read_telegram_config(filename):
	for base in (_SHARED_TG_DIR, CONFIG_DIR):
		filepath = os.path.join(base, filename)
		try:
			with open(filepath, 'r', encoding='utf-8-sig') as f:
				value = f.read().strip()
			if value:
				return value
		except (FileNotFoundError, OSError):
			continue
	return ""

telegram_chat_id = _read_telegram_config('telegram_chat_id.txt')
telegram_token = _read_telegram_config('telegram_token.txt')

real_host_url = "https://api.kiwoom.com"
paper_host_url = "https://mockapi.kiwoom.com"

real_socket_url = "wss://api.kiwoom.com:10000"
paper_socket_url = "wss://mockapi.kiwoom.com:10000"

def get_app_key():
	"""현재 설정에 맞는 app_key를 반환합니다."""
	return paper_app_key if get_is_paper_trading() else real_app_key

def get_app_secret():
	"""현재 설정에 맞는 app_secret을 반환합니다."""
	return paper_app_secret if get_is_paper_trading() else real_app_secret

def get_host_url():
	"""현재 설정에 맞는 host_url을 반환합니다."""
	return paper_host_url if get_is_paper_trading() else real_host_url

def get_socket_url():
	"""현재 설정에 맞는 socket_url을 반환합니다."""
	return paper_socket_url if get_is_paper_trading() else real_socket_url

# 하위 호환성을 위한 변수 (함수 호출로 값을 가져옴)
app_key = get_app_key()
app_secret = get_app_secret()
host_url = get_host_url()
socket_url = get_socket_url()
