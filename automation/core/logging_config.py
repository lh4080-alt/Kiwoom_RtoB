"""
봇 logging 설정 — KST 시각 + 외부 라이브러리 음소거.

호출:
    from core.logging_config import setup_logging
    setup_logging()

설계:
- StreamHandler(stdout)만 사용. cmd 래퍼가 stdout을 logs/bot.log로 redirect하므로
  FileHandler 추가하면 중복 기록.
- KSTFormatter로 시스템 시간대 무관하게 KST timestamp 출력 (unix epoch → KST 변환).
- httpx/httpcore의 INFO 로그는 매 HTTP 요청마다 한 줄이라 시끄러움 → WARNING으로 음소거.
"""
import logging
import sys
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


class KSTFormatter(logging.Formatter):
	"""시스템 시간대와 무관하게 KST로 timestamp 출력."""

	def formatTime(self, record, datefmt=None):
		ct = datetime.fromtimestamp(record.created, tz=KST)
		if datefmt:
			return ct.strftime(datefmt)
		return ct.strftime('%Y-%m-%d %H:%M:%S') + f',{int(record.msecs):03d}'


def setup_logging():
	"""봇 startup 시 1회 호출."""
	handler = logging.StreamHandler(sys.stdout)
	handler.setFormatter(KSTFormatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))

	logging.basicConfig(
		level=logging.INFO,
		handlers=[handler],
		force=True,
	)

	# 외부 라이브러리 INFO 음소거 (HTTP 요청 한 줄씩 찍혀 시끄러움)
	logging.getLogger('httpx').setLevel(logging.WARNING)
	logging.getLogger('httpcore').setLevel(logging.WARNING)
