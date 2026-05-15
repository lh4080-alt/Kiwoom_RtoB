import asyncio
import datetime
import sys
import os
import re

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from api.cancel_all_unfilled import cancel_all_unfilled_orders
from utils.market_hour import MarketHour

class CancelUnfilledScheduler:
	"""미체결 주문 일괄 취소 스케줄러"""
	
	def __init__(self, token_manager):
		self.token_manager = token_manager
		self.scheduled_time = None  # 예약된 시간 (datetime.time)
		self.interval_minutes = None  # 주기 실행 간격 (분)
		self.task = None  # 실행 중인 태스크
		self.is_running = False
	
	async def _scheduled_time_loop(self):
		"""특정 시간에 실행하는 루프"""
		while self.is_running and self.scheduled_time:
			now = datetime.datetime.now()
			target_time = datetime.datetime.combine(now.date(), self.scheduled_time)
			
			# 오늘 시간이 지났으면 내일로 설정
			if target_time <= now:
				target_time += datetime.timedelta(days=1)
			
			# 목표 시간까지 대기
			wait_seconds = (target_time - now).total_seconds()
			print(f"⏰ 미체결 주문 일괄 취소가 {self.scheduled_time.strftime('%H:%M')}에 실행되도록 예약되었습니다. ({wait_seconds:.0f}초 후)")
			
			try:
				await asyncio.sleep(wait_seconds)
				
				if self.is_running:
					# 미체결 주문 일괄 취소 실행
					await self._execute_cancel()
					
					# 다음날 같은 시간에 다시 실행하도록 설정
					# 루프가 계속 돌면서 자동으로 처리됨
			except asyncio.CancelledError:
				break
			except Exception as e:
				print(f"❌ 예약 실행 중 오류: {e}")
				await asyncio.sleep(60)  # 오류 발생 시 1분 후 재시도
	
	async def _interval_loop(self):
		"""주기적으로 실행하는 루프 (장 운영 시간에만 실행)"""
		while self.is_running and self.interval_minutes:
			try:
				# 장 운영 시간인 경우에만 실행
				if MarketHour.is_market_open_time():
					# 미체결 주문 일괄 취소 실행
					await self._execute_cancel()
					
					# 다음 실행까지 대기
					await asyncio.sleep(self.interval_minutes * 60)
				else:
					# 장 운영 시간이 아니면 1분마다 체크
					await asyncio.sleep(60)
			except asyncio.CancelledError:
				break
			except Exception as e:
				print(f"❌ 주기 실행 중 오류: {e}")
				await asyncio.sleep(60)  # 오류 발생 시 1분 후 재시도
	
	async def _execute_cancel(self):
		"""미체결 주문 일괄 취소 실행 (실패 항목 재시도 포함)"""
		# 장 운영 시간이 아니면 실행하지 않음
		if not MarketHour.is_market_open_time():
			print(f"⏸️ 장 운영 시간이 아니어서 미체결 주문 취소를 건너뜁니다.")
			return
		
		try:
			token = self.token_manager.token
			if not token:
				token = await self.token_manager.get_token()
			
			if not token:
				await tel_send("❌ 토큰 발급에 실패하여 미체결 주문 취소를 실행할 수 없습니다.")
				return
			
			# 최대 3번 시도 (초기 1번 + 재시도 2번)
			max_attempts = 3
			total_success = 0
			total_fail = 0
			total_count = 0
			
			for attempt in range(max_attempts):
				success_count, fail_count, count = await cancel_all_unfilled_orders(token=token)
				
				# 첫 시도에서만 total_count 설정
				if attempt == 0:
					total_count = count
				
				total_success += success_count
				total_fail = fail_count  # 마지막 시도의 실패 건수
				
				# 실패 항목이 없거나 마지막 시도면 종료
				if fail_count == 0 or attempt == max_attempts - 1:
					break
				
				# 재시도 전 1초 대기
				if attempt < max_attempts - 1:
					await asyncio.sleep(1)
			
			# 결과 메시지 생성
			if total_count == 0:
				await tel_send("✅ 취소할 미체결 주문이 없습니다.")
			else:
				message = f"✅ 총 {total_count}건의 미체결 주문 중 {total_success}건이 취소되었습니다."
				if total_fail > 0:
					message += f" ({total_fail}건 실패)"
				if max_attempts > 1 and total_fail > 0:
					message += f"\n(총 {max_attempts}번 시도)"
				await tel_send(message)
		except Exception as e:
			await tel_send(f"❌ 미체결 주문 일괄 취소 실행 중 오류: {e}")
	
	def start_scheduled(self, time_str):
		"""특정 시간에 실행하도록 예약"""
		try:
			# 시간 문자열 파싱 (HH:MM 형식)
			time_match = re.match(r'(\d{1,2}):(\d{2})', time_str)
			if not time_match:
				return False
			
			hour = int(time_match.group(1))
			minute = int(time_match.group(2))
			
			if not (0 <= hour <= 23 and 0 <= minute <= 59):
				return False
			
			self.scheduled_time = datetime.time(hour, minute)
			self.interval_minutes = None
			
			# 기존 태스크가 있으면 취소
			if self.task and not self.task.done():
				self.task.cancel()
			
			# 새 태스크 시작
			self.is_running = True
			self.task = asyncio.create_task(self._scheduled_time_loop())
			return True
		except Exception as e:
			print(f"❌ 예약 설정 중 오류: {e}")
			return False
	
	def start_interval(self, minutes):
		"""주기적으로 실행하도록 설정"""
		try:
			interval = int(minutes)
			if interval <= 0:
				return False
			
			self.interval_minutes = interval
			self.scheduled_time = None
			
			# 기존 태스크가 있으면 취소
			if self.task and not self.task.done():
				self.task.cancel()
			
			# 새 태스크 시작
			self.is_running = True
			self.task = asyncio.create_task(self._interval_loop())
			return True
		except Exception as e:
			print(f"❌ 주기 설정 중 오류: {e}")
			return False
	
	def stop(self):
		"""스케줄러 중지"""
		self.is_running = False
		self.scheduled_time = None
		self.interval_minutes = None
		
		if self.task and not self.task.done():
			self.task.cancel()
			self.task = None
	
	def get_status(self):
		"""현재 스케줄러 상태 반환"""
		if not self.is_running:
			return None
		
		if self.scheduled_time:
			return f"예약 실행: 매일 {self.scheduled_time.strftime('%H:%M')}"
		elif self.interval_minutes:
			return f"주기 실행: {self.interval_minutes}분마다"
		
		return None

# 전역 스케줄러 인스턴스 (BackgroundTaskManager에서 관리)
_scheduler = None

def get_scheduler(token_manager):
	"""스케줄러 인스턴스 가져오기 (싱글톤)"""
	global _scheduler
	if _scheduler is None:
		_scheduler = CancelUnfilledScheduler(token_manager)
	return _scheduler

async def cancel_unfilled_command(token_manager, args=None):
	"""
	/ccu 명령어를 처리합니다.
	
	사용법:
	- /ccu: 즉시 실행
	- /ccu 15:20: 매일 15시 20분에 예약 실행
	- /ccu 30: 30분 주기로 실행
	- /ccu off: 예약/주기 실행 종료
	"""
	try:
		scheduler = get_scheduler(token_manager)
		
		# 인자가 없으면 즉시 실행
		if not args or args.strip() == '':
			await tel_send("🔄 미체결 주문을 조회하고 취소합니다...")
			
			token = token_manager.token
			if not token:
				token = await token_manager.get_token()
			
			if not token:
				await tel_send("❌ 토큰 발급에 실패했습니다.")
				return False
			
			# 최대 3번 시도 (초기 1번 + 재시도 2번)
			max_attempts = 3
			total_success = 0
			total_fail = 0
			total_count = 0
			
			for attempt in range(max_attempts):
				success_count, fail_count, count = await cancel_all_unfilled_orders(token=token)
				
				# 첫 시도에서만 total_count 설정
				if attempt == 0:
					total_count = count
				
				total_success += success_count
				total_fail = fail_count  # 마지막 시도의 실패 건수
				
				# 실패 항목이 없거나 마지막 시도면 종료
				if fail_count == 0 or attempt == max_attempts - 1:
					break
				
				# 재시도 전 1초 대기
				if attempt < max_attempts - 1:
					await asyncio.sleep(1)
			
			# 결과 메시지 생성
			if total_count == 0:
				await tel_send("✅ 취소할 미체결 주문이 없습니다.")
			else:
				message = f"✅ 총 {total_count}건의 미체결 주문 중 {total_success}건이 취소되었습니다."
				if total_fail > 0:
					message += f" ({total_fail}건 실패)"
				if max_attempts > 1 and total_fail > 0:
					message += f"\n(총 {max_attempts}번 시도)"
				await tel_send(message)
			
			return True
		
		args_lower = args.strip().lower()
		
		# off 명령어: 스케줄러 중지
		if args_lower == 'off':
			if not scheduler.is_running:
				await tel_send("⚠️ 실행 중인 예약/주기 실행이 없습니다.")
				return True
			
			scheduler.stop()
			await tel_send("✅ 미체결 주문 일괄 취소 예약/주기 실행이 중지되었습니다.")
			return True
		
		# 시간 형식 확인 (HH:MM) - 원본 args 사용
		time_match = re.match(r'(\d{1,2}):(\d{2})', args.strip())
		if time_match:
			hour = int(time_match.group(1))
			minute = int(time_match.group(2))
			
			if not (0 <= hour <= 23 and 0 <= minute <= 59):
				await tel_send("❌ 잘못된 시간 형식입니다. 예: /ccu 15:20")
				return False
			
			time_str = f"{hour}:{minute:02d}"
			if scheduler.start_scheduled(time_str):
				await tel_send(f"✅ 매일 {hour:02d}:{minute:02d}에 미체결 주문 일괄 취소가 실행되도록 예약되었습니다.")
				return True
			else:
				await tel_send("❌ 예약 설정에 실패했습니다.")
				return False
		
		# 숫자 확인 (주기 실행, 분 단위)
		try:
			minutes = int(args.strip())
			if minutes <= 0:
				await tel_send("❌ 주기는 1분 이상이어야 합니다.")
				return False
			
			if scheduler.start_interval(minutes):
				await tel_send(f"✅ {minutes}분 주기로 미체결 주문 일괄 취소가 실행되도록 설정되었습니다.")
				return True
			else:
				await tel_send("❌ 주기 설정에 실패했습니다.")
				return False
		except ValueError:
			await tel_send("❌ 사용법: /ccu [시간|주기|off]\n예: /ccu, /ccu 15:20, /ccu 30, /ccu off")
			return False
		
	except Exception as e:
		await tel_send(f"❌ /ccu 명령어 실행 중 오류: {e}")
		return False
