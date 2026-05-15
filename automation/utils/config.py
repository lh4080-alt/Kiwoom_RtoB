import os

# config 폴더의 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, 'config')

def read_config_file(filename):
	"""config 폴더에서 텍스트 파일을 읽어서 값을 반환"""
	filepath = os.path.join(CONFIG_DIR, filename)
	try:
		with open(filepath, 'r', encoding='utf-8') as f:
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
telegram_chat_id = read_config_file('telegram_chat_id.txt')
telegram_token = read_config_file('telegram_token.txt')

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

