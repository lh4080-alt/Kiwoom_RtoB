import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from utils.market_hour import MarketHour

async def chart_command(settings_manager, background_task_manager, start_callback, stop_callback, x, y):
	"""chart 명령어를 처리합니다 - 차트 설정 수정 (x분봉, y분봉, y분마다 체크)"""
	try:
		chart_short = int(x)
		chart_long = int(y)
		
		if chart_short <= 0 or chart_long <= 0:
			await tel_send("❌ 차트 값은 1 이상이어야 합니다")
			return False
		
		# 장기가 단기보다 길지 않으면 오류
		if chart_long <= chart_short:
			await tel_send(f"❌ 장기 분봉({chart_long}분봉)은 단기 분봉({chart_short}분봉)보다 길어야 합니다")
			return False
		
		check_interval = chart_long
		
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
				await tel_send("🔄 차트 설정 변경을 위해 프로세스를 재시작합니다...")
				await stop_callback(set_auto_start_false=False, feature_numbers=''.join(map(str, previous_features)))
				await asyncio.sleep(1)  # 잠시 대기
		
		if settings_manager.update_setting('chart_short', chart_short) and settings_manager.update_setting('chart_long', chart_long):
			await tel_send(
				f"✅ 차트 설정이 변경되었습니다\n"
				f"   단기봉: {chart_short}분봉\n"
				f"   장기봉: {chart_long}분봉\n"
				f"   체크 주기: {check_interval}분마다"
			)
			
			# 실행 중이었으면 다시 start (이전 기능들)
			if was_running and previous_features and MarketHour.is_market_open_time():
				await asyncio.sleep(1)
				feature_str = ''.join(map(str, previous_features))
				await start_callback(is_paper_trading=True, feature_numbers=feature_str)
			
			return True
		else:
			await tel_send("❌ 차트 설정 변경에 실패했습니다")
			return False
	except ValueError:
				await tel_send("❌ 잘못된 숫자 형식입니다. 예: chart 5 20")
				return False
	except Exception as e:
				await tel_send(f"❌ chart 명령어 실행 중 오류: {e}")
				return False

