import requests
import os
from config import telegram_token

# config 폴더의 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, 'config')
CHAT_ID_FILE = os.path.join(CONFIG_DIR, 'telegram_chat_id.txt')

url = f"https://api.telegram.org/bot{telegram_token}/getUpdates"
response = requests.get(url)
data = response.json()

# 응답 확인
if data.get('ok') and data.get('result') and len(data['result']) > 0:
    # 가장 최근 메시지의 chat id 추출
    latest_update = data['result'][-1]
    if 'message' in latest_update and 'chat' in latest_update['message']:
        chat_id = latest_update['message']['chat']['id']
        
        # chat id를 파일에 저장
        with open(CHAT_ID_FILE, 'w', encoding='utf-8') as f:
            f.write(str(chat_id))
        
        print(f"성공! Chat ID ({chat_id})가 {CHAT_ID_FILE}에 저장되었습니다.")
    else:
        print("오류: 응답에서 chat 정보를 찾을 수 없습니다.")
        print("telegram_chat_id를 확인하고 해당 텔레그램 톡방에 톡을 하나 보낸 뒤 다시 시도하세요.")
else:
    print("오류: Telegram API 응답이 올바르지 않습니다.")
    print("telegram_chat_id를 확인하고 해당 텔레그램 톡방에 톡을 하나 보낸 뒤 다시 시도하세요.")
    print(f"응답 내용: {data}")