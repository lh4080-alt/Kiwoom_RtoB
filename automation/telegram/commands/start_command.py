import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from utils.market_hour import MarketHour
from telegram.commands.feature_parser import parse_feature_numbers
from telegram.commands.help_command import send_user_guide

async def show_start_menu():
	"""start 명령어 메뉴 표시"""
	menu = """📋 사용 가능한 기능 조합:

1️⃣ 조건식 검색 매수
2️⃣ 수익율 매도
3️⃣ 골든크로스 매수
4️⃣ 데드크로스 매도
5️⃣ 트레일링 스탑 매도
6️⃣ 돌파 매수
7️⃣ 그리드 트레이딩
8️⃣ 분할 트레이딩

사용법:
• start 1 - 조건식 검색 매수만
• start 2 - 수익율 매도만
• start 5 - 트레일링 스탑 매도만
• start 6 - 돌파 매수만
• start 7 - 그리드 트레이딩만
• start 8 - 분할 트레이딩만
• start 12 - 조건식 검색 매수 + 수익율 매도
• start 14 - 조건식 검색 매수 + 데드크로스 매도
• start 234 - 수익율 매도 + 골든크로스 매수 + 데드크로스 매도
• start 12345678 - 모든 기능

예시: start 14, start 5, start 6, start 7, start 8"""
	await tel_send(menu)
	await send_user_guide()

async def start_command(websocket, token_manager, settings_manager, background_task_manager, is_paper_trading=True, feature_numbers=None):
	"""start 명령어를 처리합니다."""
	try:
		# feature_numbers가 None이면 메뉴 표시
		if feature_numbers is None:
			await show_start_menu()
			return True
		
		# 숫자 조합 파싱
		features = parse_feature_numbers(feature_numbers)
		
		if not features:
			await tel_send("❌ 잘못된 기능 번호입니다. 1, 2, 3, 4, 5, 6, 7, 8 중에서 선택해주세요.")
			return False
		
		# is_paper_trading 설정을 가장 먼저 업데이트
		if not settings_manager.update_setting('is_paper_trading', is_paper_trading):
			await tel_send("❌ is_paper_trading 설정 파일 업데이트 실패")
			return False
		
		# 모드 전환 시 기존 토큰 초기화 (새 모드에 맞는 토큰을 받기 위해)
		token_manager.reset_token()
		
		# 새로운 토큰 발급
		token = await token_manager.get_token()
		if not token:
			await tel_send("❌ 토큰 발급에 실패했습니다")
			return False
		
		# auto_start를 true로 설정
		if not settings_manager.update_setting('auto_start', True):
			await tel_send("❌ 설정 파일 업데이트 실패")
			return False
		
		# 마지막으로 사용한 기능 조합 저장 (다음날 자동 시작을 위해)
		if not settings_manager.update_setting('last_feature_numbers', feature_numbers):
			await tel_send("❌ 기능 조합 저장 실패")
			return False
		
		# 장이 열리지 않았을 때는 auto_start만 설정하고 메시지 전송
		if not MarketHour.is_market_open_time():
			await tel_send(f"⏰ 장이 열리지 않았습니다. 장 시작 시간({MarketHour.MARKET_START_HOUR:02d}:{MarketHour.MARKET_START_MINUTE:02d})에 자동으로 시작됩니다.")
			# 장 시간이 아니면 기능들을 시작하지 않음 (장 시작 시간에 자동으로 시작됨)
			return True
		
		# 선택된 기능들 시작 (기능 1의 경우 재시도 로직은 background_task_manager에서 처리)
		success = await background_task_manager.start_features(features, token)
		
		return success
			
	except Exception as e:
		await tel_send(f"❌ start 명령어 실행 중 오류: {e}\n계속 재시작이 되지 않으면 'start' 명령어를 다시 입력해주세요.")
		return False

