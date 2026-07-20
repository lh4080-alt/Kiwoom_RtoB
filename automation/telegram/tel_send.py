import asyncio
import json
import sys
import os
import time

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.get_setting import get_setting
from utils.config import telegram_token, telegram_chat_id
from utils.rate_limiter import requests

# 공유 봇 토큰(@Kwoom_Basic_bot)을 여러 봇이 같이 쓰므로 발송 간 최소 간격 유지
# (2026-07-16 Lee 지시: 초당 1건 수준 권장 — 0.7초 사용)
_MIN_SEND_INTERVAL_SEC = 0.7
_send_lock = asyncio.Lock()
_last_send_monotonic = 0.0

async def tel_send(message, parse_mode=None):
	global _last_send_monotonic
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

	async with _send_lock:
		wait = _MIN_SEND_INTERVAL_SEC - (time.monotonic() - _last_send_monotonic)
		if wait > 0:
			await asyncio.sleep(wait)
		try:
			response = await requests.post(url, json=data)
			response_data = response.json()
			print(response_data)
			return response_data
		except Exception as e:
			print(f"Telegram 메시지 전송 중 오류 발생: {e}")
		finally:
			_last_send_monotonic = time.monotonic()

if __name__ == "__main__":
	asyncio.run(tel_send("키움 API 테스트"))
