import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.get_setting import get_setting
from utils.config import telegram_token, telegram_chat_id
from utils.rate_limiter import requests

async def tel_send(message, parse_mode=None):
	url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"

	# is_paper_trading 읽기
	is_paper_trading = get_setting('is_paper_trading', True)

	# is_paper_trading이 문자열인 경우 boolean으로 변환
	if isinstance(is_paper_trading, str):
		is_paper_trading = is_paper_trading.lower() in ('true', '1', 'yes')

	# paper trading 여부에 따라 접두사 설정
	trading_type = "모의" if is_paper_trading else "실투"

	data = {
		"chat_id": telegram_chat_id,
		"text": f"[{trading_type}] {message}"
	}
	if parse_mode:
		data["parse_mode"] = parse_mode

	try:
		response = await requests.post(url, json=data)
		response_data = response.json()
		print(response_data)
		return response_data
	except Exception as e:
		print(f"Telegram 메시지 전송 중 오류 발생: {e}")

if __name__ == "__main__":
	tel_send("키움 API 테스트")

