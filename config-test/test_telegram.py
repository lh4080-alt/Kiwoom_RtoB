import requests
import os
import sys

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
        return None

# 텔레그램 토큰과 채팅 ID 읽기
telegram_token = read_config_file('telegram_token.txt')
telegram_chat_id = read_config_file('telegram_chat_id.txt')

# 파일 읽기 확인
if not telegram_token:
    print("[오류] config/telegram_token.txt 파일을 찾을 수 없거나 비어있습니다.")
    sys.exit(1)

if not telegram_chat_id:
    print("[오류] config/telegram_chat_id.txt 파일을 찾을 수 없거나 비어있습니다.")
    sys.exit(1)

print(f"텔레그램 토큰: {telegram_token[:10]}...")
print(f"채팅 ID: {telegram_chat_id}")
print()

# 텔레그램 메시지 전송
url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
data = {
    "chat_id": telegram_chat_id,
    "text": "✅ 텔레그램 설정이 정상적으로 작동합니다!"
}

try:
    print("텔레그램으로 테스트 메시지를 전송 중...")
    response = requests.post(url, json=data, timeout=10)
    response_data = response.json()
    
    if response_data.get('ok'):
        print("[성공] 텔레그램 메시지가 성공적으로 전송되었습니다!")
        print(f"메시지 ID: {response_data.get('result', {}).get('message_id', 'N/A')}")
    else:
        print("[오류] 텔레그램 메시지 전송에 실패했습니다.")
        print(f"응답 내용: {response_data}")
        sys.exit(1)
        
except requests.exceptions.RequestException as e:
    print(f"[오류] 네트워크 오류가 발생했습니다: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[오류] 예상치 못한 오류가 발생했습니다: {e}")
    sys.exit(1)

