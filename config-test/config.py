import os

# config 폴더의 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, 'config')

def read_config_file(filename):
    """config 폴더에서 텍스트 파일을 읽어서 값을 반환"""
    filepath = os.path.join(CONFIG_DIR, filename)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

# 실제 투자로 진행할 시 True를 False로 변경
is_paper_trading = True

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

app_key = paper_app_key if is_paper_trading else real_app_key
app_secret = paper_app_secret if is_paper_trading else real_app_secret
host_url = paper_host_url if is_paper_trading else real_host_url
socket_url = paper_socket_url if is_paper_trading else real_socket_url