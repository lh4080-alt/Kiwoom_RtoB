import asyncio
import datetime
import sys
import os
import shutil

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import telegram_token
from utils.rate_limiter import requests
from telegram.chat_command import ChatCommand
from utils.get_setting import get_setting
from utils.market_hour import MarketHour
from telegram.commands.start_command import show_start_menu
from telegram.tel_send import tel_send

def migrate_config_files():
	"""기존 config 폴더의 설정 파일들을 config/data 폴더로 마이그레이션합니다."""
	try:
		script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		base_dir = os.path.dirname(script_dir)
		config_dir = os.path.join(base_dir, 'config')
		data_dir = os.path.join(config_dir, 'data')
		
		# data 폴더가 없으면 생성
		if not os.path.exists(data_dir):
			os.makedirs(data_dir, exist_ok=True)
		
		# 마이그레이션할 파일 목록
		files_to_migrate = [
			'settings.json',
			'sold_stocks.json',  # last_held_stocks.json으로 자동 변경됨 (sold_stocks_manager.py에서 처리)
			'trailing_status.json',
			'grid_status.json'
		]
		
		migrated_count = 0
		for filename in files_to_migrate:
			# grid_status.json은 automation 폴더에서도 마이그레이션 필요
			if filename == 'grid_status.json':
				# automation 폴더에서 마이그레이션
				old_path = os.path.join(script_dir, filename)
				new_path = os.path.join(data_dir, filename)
				
				if os.path.exists(old_path) and not os.path.exists(new_path):
					try:
						shutil.move(old_path, new_path)
						print(f"✓ {filename} 파일을 config/data 폴더로 이동했습니다.")
						migrated_count += 1
					except Exception as e:
						print(f"⚠️ {filename} 파일 이동 실패: {e}")
				elif os.path.exists(old_path) and os.path.exists(new_path):
					# 둘 다 존재하는 경우, 기존 파일을 삭제 (새 파일이 우선)
					try:
						os.remove(old_path)
						print(f"✓ {filename} 파일의 중복을 제거했습니다 (기존 파일 삭제).")
					except Exception as e:
						print(f"⚠️ {filename} 기존 파일 삭제 실패: {e}")
			else:
				# 다른 파일들은 config 폴더에서 마이그레이션
				old_path = os.path.join(config_dir, filename)
				new_path = os.path.join(data_dir, filename)
				
				# 기존 파일이 있고 새 위치에 없으면 마이그레이션
				if os.path.exists(old_path) and not os.path.exists(new_path):
					try:
						shutil.move(old_path, new_path)
						print(f"✓ {filename} 파일을 config/data 폴더로 이동했습니다.")
						migrated_count += 1
					except Exception as e:
						print(f"⚠️ {filename} 파일 이동 실패: {e}")
				elif os.path.exists(old_path) and os.path.exists(new_path):
					# 둘 다 존재하는 경우, 기존 파일을 삭제 (새 파일이 우선)
					try:
						os.remove(old_path)
						print(f"✓ {filename} 파일의 중복을 제거했습니다 (기존 파일 삭제).")
					except Exception as e:
						print(f"⚠️ {filename} 기존 파일 삭제 실패: {e}")
		
		if migrated_count > 0:
			print(f"설정 파일 마이그레이션 완료: {migrated_count}개 파일 이동")
		
	except Exception as e:
		print(f"설정 파일 마이그레이션 중 오류 발생: {e}")

class MainApp:
	def __init__(self):
		self.chat_command = ChatCommand()
		self.last_update_id = 0
		self.telegram_url = f"https://api.telegram.org/bot{telegram_token}/getUpdates"
		self.keep_running = True
		self.today_started = False  # 오늘 start가 실행되었는지 추적
		self.today_stopped = False  # 오늘 stop이 실행되었는지 추적
		self.last_check_date = None  # 마지막으로 확인한 날짜
		self.last_end_report_sent = None  # 마지막 장 마감 보고서 발송 날짜
		
	async def skip_pending_updates(self):
		"""프로그램 시작 시 모든 미처리 업데이트를 건너뜁니다."""
		try:
			total_skipped = 0
			# 모든 미처리 업데이트를 반복적으로 가져오기
			while True:
				params = {
					'offset': self.last_update_id + 1,
					'limit': 100,  # 한 번에 최대 100개의 업데이트 가져오기
					'timeout': 1
				}
				response = await requests.get(self.telegram_url, params=params)
				data = response.json()
				
				if data.get('ok'):
					updates = data.get('result', [])
					if updates:
						# 가장 최신 update_id로 설정
						self.last_update_id = max(update['update_id'] for update in updates)
						total_skipped += len(updates)
					else:
						# 더 이상 업데이트가 없으면 종료
						break
				else:
					break
			
			if total_skipped > 0:
				print(f"미처리 업데이트 {total_skipped}개를 건너뛰고 update_id를 {self.last_update_id}로 설정했습니다.")
			else:
				print("미처리 업데이트가 없습니다.")
		except Exception as e:
			print(f"미처리 업데이트 건너뛰기 실패: {e}")
	
	async def get_chat_updates(self):
		"""텔레그램 채팅 업데이트를 가져옵니다."""
		try:
			params = {
				'offset': self.last_update_id + 1,
				'timeout': 10
			}
			response = await requests.get(self.telegram_url, params=params)
			data = response.json()
			
			if data.get('ok'):
				updates = data.get('result', [])
				for update in updates:
					self.last_update_id = update['update_id']
					
					if 'message' in update and 'text' in update['message']:
						text = update['message']['text']
						print(f"받은 메시지: {text}")
						return text
			return None
		except Exception as e:
			print(f"채팅 업데이트 가져오기 실패: {e}")
			return None
	
	
	async def check_market_timing(self):
		"""장 시작/종료 시간을 확인하고 자동 실행합니다."""
		auto_start = get_setting('auto_start', False)
		today = datetime.datetime.now().date()
		
		# 새로운 날이 되면 플래그 리셋
		if self.last_check_date != today:
			self.today_started = False
			self.today_stopped = False
			self.last_check_date = today
		
		# 장 시작 시간 체크 (장 시작 1분 후에 종목 선정이 이루어지므로, 장 시작 시간에 start 실행)
		if MarketHour.is_market_start_time() and auto_start and not self.today_started:
			# 이미 실행 중인 기능이 있는지 확인 (재로그인 후 자동 복구와의 중복 실행 방지)
			if self.chat_command.background_task_manager.is_running_any:
				print(f"장 시작 시간이지만 이미 실행 중인 기능이 있어 자동 start를 건너뜁니다.")
				self.today_started = True  # 오늘 start 실행 완료 표시 (중복 방지)
				return
			
			print(f"장 시작 시간({MarketHour.get_start_hour():02d}:{MarketHour.get_start_minute():02d})입니다. 자동으로 start 명령을 실행합니다.")
			# 마지막으로 사용했던 기능 조합을 가져와서 자동 시작
			last_feature_numbers = self.chat_command.settings_manager.get_setting('last_feature_numbers', None)
			is_paper_trading = self.chat_command.settings_manager.get_setting('is_paper_trading', True)
			
			if last_feature_numbers:
				print(f"저장된 기능 조합으로 자동 시작: {last_feature_numbers}")
				await self.chat_command.start(is_paper_trading=is_paper_trading, feature_numbers=last_feature_numbers)
			else:
				print("저장된 기능 조합이 없어 메뉴를 표시합니다.")
				await self.chat_command.start(is_paper_trading=True, feature_numbers=None)  # 메뉴 표시
			self.today_started = True  # 오늘 start 실행 완료 표시
		
		# 장 종료 시간 체크
		if MarketHour.is_market_end_time() and not self.today_stopped:
			print(f"장 종료 시간({MarketHour.get_end_hour():02d}:{MarketHour.get_end_minute():02d})입니다.")
			
			# 일별 보고서 발송 (하루에 한 번만)
			if self.last_end_report_sent != today:
				print("자동으로 일별 보고서를 발송합니다.")
				await self.chat_command.report()
				self.last_end_report_sent = today
			
			# 모의투자 기간 만료 안내 메시지 전송
			await tel_send("🔔 모의투자 기간이 만료되면 모의투자를 다시 신청하여 앱 키와 시크릿을 새로 등록해야 합니다.")
			
			# 장 마감 시 모든 기능 중지 (auto_start는 유지하여 다음날 자동 시작)
			if self.chat_command.background_task_manager.is_running_any:
				print("장 마감으로 인해 프로세스를 중지합니다.")
				await self.chat_command.background_task_manager.stop_all()
				
				# 다음 장 시작 시간 계산
				now = datetime.datetime.now()
				start_hour = MarketHour.get_start_hour()
				start_minute = MarketHour.get_start_minute()
				
				# 다음 평일 찾기
				next_market_day = now
				days_to_add = 1
				while True:
					next_market_day = now + datetime.timedelta(days=days_to_add)
					if next_market_day.weekday() < 5:  # 평일 (0=월요일, 4=금요일)
						break
					days_to_add += 1
				
				# 다음 장 시작 시간 설정
				next_start_time = next_market_day.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
				
				# 요일 이름
				weekday_names = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
				next_weekday = weekday_names[next_start_time.weekday()]
				
				# 자동매매 종료 알림 메시지
				next_start_str = next_start_time.strftime(f"%Y년 %m월 %d일 {next_weekday} %H:%M")
				await tel_send(f"⏹️ 장 마감으로 인해 자동매매가 종료되었습니다.\n\n🔄 다음 자동매매 시작 시간: {next_start_str}")
			
			self.today_stopped = True  # 오늘 stop 실행 완료 표시
	
	async def start_websocket(self):
		"""웹소켓을 시작합니다. 연결 실패 시 재시도합니다."""
		max_retries = 10
		retry_delay = 3
		
		for attempt in range(max_retries):
			try:
				# 토큰 발급 (프로그램 시작 시점이므로 기존 캐시된 토큰이 있어도 안전하게 새로 발급)
				token = await self.chat_command.token_manager.get_token()
				if not token:
					print(f"토큰 발급 실패, {retry_delay}초 후 재시도합니다... ({attempt + 1}/{max_retries})")
					if attempt < max_retries - 1:
						await asyncio.sleep(retry_delay)
						retry_delay = min(retry_delay * 1.2, 10)
					continue
				
				# 웹소켓 시작
				success = await self.chat_command.websocket.start(token)
				if success:
					print("웹소켓 연결 성공")
					return True
				else:
					print(f"웹소켓 연결 실패, {retry_delay}초 후 재시도합니다... ({attempt + 1}/{max_retries})")
					if attempt < max_retries - 1:
						await asyncio.sleep(retry_delay)
						retry_delay = min(retry_delay * 1.2, 10)
					
			except Exception as e:
				print(f"웹소켓 시작 중 오류 발생, {retry_delay}초 후 재시도합니다... ({attempt + 1}/{max_retries}): {e}")
				if attempt < max_retries - 1:
					await asyncio.sleep(retry_delay)
					retry_delay = min(retry_delay * 1.2, 10)
		
		# 10회 이상 실패 시 텔레그램 고지
		from telegram.tel_send import tel_send
		await tel_send("❌ 웹소켓 연결이 10회 연속 실패했습니다. 프로그램을 재시작해주세요.")
		return False
	
	async def shutdown(self):
		"""프로그램을 완전히 종료합니다."""
		# 종료 전 알림 메시지 전송
		await tel_send("🔌 프로그램 종료를 시작합니다...\n모든 기능을 중지하고 연결을 종료합니다.")
		
		print("\n프로그램을 종료합니다...")
		self.keep_running = False
		await self.chat_command.background_task_manager.stop_all()
		await self.chat_command.websocket.stop()
		
		# 종료 완료 메시지 전송
		await tel_send("✅ 프로그램이 종료되었습니다.")
	
	async def run(self):
		"""메인 실행 루프"""
		print("채팅 모니터링을 시작합니다...")
		
		# ChatCommand에 main_app 참조 전달
		self.chat_command.main_app = self
		
		# 프로그램 시작 시 모든 미처리 업데이트 건너뛰기 (이전 메시지 무시)
		await self.skip_pending_updates()
		
		# 프로그램 시작 시 웹소켓 시작
		await self.start_websocket()
		
		# 프로그램 시작 시 모의투자 기간 만료 안내 메시지 전송
		await tel_send("🔔 모의투자 기간이 만료되면 모의투자를 다시 신청하여 앱 키와 시크릿을 새로 등록해야 합니다.")
		
		# 프로그램 시작 시 start 메뉴 전송
		await show_start_menu()
		
		try:
			while self.keep_running:
				# 채팅 메시지 확인
				message = await self.get_chat_updates()
				if message:
					await self.chat_command.process_command(message)
				
				# 장 시작/종료 시간 확인
				await self.check_market_timing()
				
				# 1초 대기
				await asyncio.sleep(1)
				
		except KeyboardInterrupt:
			print("\n프로그램을 종료합니다...")
			self.keep_running = False
			await self.chat_command.background_task_manager.stop_all()
			await self.chat_command.websocket.stop()

async def main():
	# 프로그램 시작 시 설정 파일 마이그레이션
	migrate_config_files()
	
	app = MainApp()
	await app.run()

if __name__ == '__main__':
	asyncio.run(main())

