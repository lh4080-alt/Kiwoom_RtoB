import datetime
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.get_setting import get_setting

class MarketHourMeta(type):
	"""MarketHour 클래스의 메타클래스 - 동적 속성 접근 지원"""
	def __getattr__(cls, name):
		"""동적 속성 접근을 위한 메서드"""
		if name == 'MARKET_START_HOUR':
			return cls._get_start_hour()
		elif name == 'MARKET_START_MINUTE':
			return cls._get_start_minute()
		elif name == 'MARKET_END_HOUR':
			return cls._get_end_hour()
		elif name == 'MARKET_END_MINUTE':
			return cls._get_end_minute()
		raise AttributeError(f"'{cls.__name__}' object has no attribute '{name}'")

class MarketHour(metaclass=MarketHourMeta):
	"""장 시간 관련 상수 및 메서드를 관리하는 클래스"""
	
	@classmethod
	def _get_start_hour(cls):
		"""settings.json에서 장 시작 시를 가져옵니다."""
		return get_setting('market_start_hour', 9)
	
	@classmethod
	def _get_start_minute(cls):
		"""settings.json에서 장 시작 분을 가져옵니다."""
		return get_setting('market_start_minute', 0)
	
	@classmethod
	def _get_end_hour(cls):
		"""settings.json에서 장 종료 시를 가져옵니다."""
		return get_setting('market_end_hour', 15)
	
	@classmethod
	def _get_end_minute(cls):
		"""settings.json에서 장 종료 분을 가져옵니다."""
		return get_setting('market_end_minute', 30)
	
	# 하위 호환성을 위한 클래스 메서드
	@classmethod
	def get_start_hour(cls):
		"""장 시작 시를 반환합니다."""
		return cls._get_start_hour()
	
	@classmethod
	def get_start_minute(cls):
		"""장 시작 분을 반환합니다."""
		return cls._get_start_minute()
	
	@classmethod
	def get_end_hour(cls):
		"""장 종료 시를 반환합니다."""
		return cls._get_end_hour()
	
	@classmethod
	def get_end_minute(cls):
		"""장 종료 분을 반환합니다."""
		return cls._get_end_minute()
	
	@staticmethod
	def _is_weekday():
		"""평일인지 확인합니다."""
		return datetime.datetime.now().weekday() < 5
	
	@staticmethod
	def _get_market_time(hour, minute):
		"""장 시간을 반환합니다."""
		now = datetime.datetime.now()
		return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
	
	@classmethod
	def is_market_open_time(cls):
		"""현재 시간이 장 시간인지 확인합니다."""
		if not cls._is_weekday():
			return False
		now = datetime.datetime.now()
		market_open = cls._get_market_time(cls._get_start_hour(), cls._get_start_minute())
		market_close = cls._get_market_time(cls._get_end_hour(), cls._get_end_minute())
		return market_open <= now <= market_close
	
	@classmethod
	def is_market_start_time(cls):
		"""현재 시간이 장 시작 시간인지 확인합니다."""
		if not cls._is_weekday():
			return False
		now = datetime.datetime.now()
		market_start = cls._get_market_time(cls._get_start_hour(), cls._get_start_minute())
		return now >= market_start and (now - market_start).seconds < 60  # 1분 이내
	
	@classmethod
	def is_market_end_time(cls):
		"""현재 시간이 장 종료 시간인지 확인합니다."""
		if not cls._is_weekday():
			return False
		now = datetime.datetime.now()
		market_end = cls._get_market_time(cls._get_end_hour(), cls._get_end_minute())
		return now >= market_end and (now - market_end).seconds < 60  # 1분 이내

