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
- 로그 레벨 토글: settings.json `log_level` (DEBUG/INFO/WARNING/ERROR) 또는 환경변수
  `LOG_LEVEL` 우선. 평시 INFO, 디버그 필요 시 DEBUG로 켜고 봇 재시작.
"""
import logging
import os
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


def _resolve_level() -> int:
	"""환경변수 LOG_LEVEL 우선, 없으면 settings.json `log_level`, 둘 다 없으면 INFO."""
	env_level = os.environ.get('LOG_LEVEL')
	if env_level:
		return getattr(logging, env_level.upper(), logging.INFO)
	try:
		# 지연 import — setup_logging 호출 시점엔 sys.path 이미 세팅되어 있음
		from utils.get_setting import get_setting
		name = str(get_setting('log_level', 'INFO')).upper()
		return getattr(logging, name, logging.INFO)
	except Exception:
		return logging.INFO


def setup_logging():
	"""봇 startup 시 1회 호출."""
	fmt = KSTFormatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
	handler = logging.StreamHandler(sys.stdout)
	handler.setFormatter(fmt)
	handlers = [handler]

	# 파일 로깅 추가 (run.bat 콘솔 전용 운영 대비 — SSH로 추적 가능, 콘솔 QuickEdit 멈춤과 무관).
	# 별도 파일(bot_app.log)이라 cmd 래퍼 stdout→bot.log redirect와 중복 안 됨. 회전 5MB×3.
	try:
		from logging.handlers import RotatingFileHandler
		_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
		_logdir = os.path.join(_root, 'logs')
		os.makedirs(_logdir, exist_ok=True)
		fh = RotatingFileHandler(os.path.join(_logdir, 'bot_app.log'),
		                         maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
		fh.setFormatter(fmt)
		handlers.append(fh)
	except Exception:
		pass  # 파일 로깅 실패해도 콘솔(stdout)은 유지

	logging.basicConfig(
		level=_resolve_level(),
		handlers=handlers,
		force=True,
	)

	# 외부 라이브러리 INFO 음소거 (HTTP 요청 한 줄씩 찍혀 시끄러움)
	logging.getLogger('httpx').setLevel(logging.WARNING)
	logging.getLogger('httpcore').setLevel(logging.WARNING)
