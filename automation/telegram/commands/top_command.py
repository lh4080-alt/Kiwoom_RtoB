import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from utils.market_hour import MarketHour

async def top_command(settings_manager, background_task_manager, start_callback, stop_callback, number):
	"""top 명령어를 처리합니다 - stock_count 수정"""
	try:
		count = int(number)
		if count <= 0:
			await tel_send("❌ 종목 개수는 1 이상이어야 합니다")
			return False
		
		if count > 20:
			await tel_send("❌ 종목 개수는 20 이하여야 합니다")
			return False
		
		# 실행 중이면 stop 후 start
		was_running = background_task_manager.is_running_any
		# 이전에 실행 중이었던 기능들 저장 (기능 3, 4 관련)
		previous_features = []
		if was_running:
			if 3 in background_task_manager.active_features:
				previous_features.append(3)
			if 4 in background_task_manager.active_features:
				previous_features.append(4)
			if previous_features:
				await tel_send("🔄 종목 개수 변경을 위해 프로세스를 재시작합니다...")
				await stop_callback(set_auto_start_false=False, feature_numbers=''.join(map(str, previous_features)))
				await asyncio.sleep(1)  # 잠시 대기
		
		if settings_manager.update_setting('stock_count', count):
			await tel_send(f"✅ 종목 선정 개수가 {count}개로 설정되었습니다")
			
			# 실행 중이었으면 다시 start (이전 기능들)
			if was_running and previous_features and MarketHour.is_market_open_time():
				await asyncio.sleep(1)
				feature_str = ''.join(map(str, previous_features))
				await start_callback(is_paper_trading=True, feature_numbers=feature_str)
			
			return True
		else:
			await tel_send("❌ 종목 선정 개수 설정에 실패했습니다")
			return False
	except ValueError:
				await tel_send("❌ 잘못된 숫자 형식입니다. 예: top 10")
				return False
	except Exception as e:
				await tel_send(f"❌ top 명령어 실행 중 오류: {e}")
				return False

