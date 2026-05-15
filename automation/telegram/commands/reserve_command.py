import asyncio
import datetime
import json
import os
import re
import sys

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send

class ReserveScheduler:
	"""예약 명령어 스케줄러"""
	
	def __init__(self, script_dir, process_command_callback):
		self.script_dir = script_dir
		self.process_command_callback = process_command_callback
		# 다른 설정 파일들과 동일한 경로 계산 방식 사용
		base_dir = os.path.dirname(script_dir)
		config_dir = os.path.join(base_dir, 'config')
		data_dir = os.path.join(config_dir, 'data')
		
		# data 폴더가 없으면 생성
		if not os.path.exists(data_dir):
			os.makedirs(data_dir, exist_ok=True)
		
		self.reservations_file = os.path.join(data_dir, 'reservations.json')
		self.task = None
		self.is_running = False
	
	def _load_reservations(self):
		"""예약 목록을 파일에서 로드"""
		try:
			if os.path.exists(self.reservations_file):
				with open(self.reservations_file, 'r', encoding='utf-8') as f:
					data = json.load(f)
					return data.get('reservations', []), data.get('next_id', 1)
			return [], 1
		except Exception as e:
			print(f"❌ 예약 목록 로드 실패: {e}")
			return [], 1
	
	def _save_reservations(self, reservations, next_id):
		"""예약 목록을 파일에 저장"""
		try:
			data = {
				'reservations': reservations,
				'next_id': next_id
			}
			with open(self.reservations_file, 'w', encoding='utf-8') as f:
				json.dump(data, f, ensure_ascii=False, indent=2)
			return True
		except Exception as e:
			print(f"❌ 예약 목록 저장 실패: {e}")
			return False
	
	async def _check_and_execute(self):
		"""예약된 명령어를 확인하고 실행 (매일 해당 시간에 실행)"""
		while self.is_running:
			try:
				now = datetime.datetime.now()
				current_time_str = now.strftime('%H:%M')
				current_date_str = now.strftime('%Y-%m-%d')
				
				reservations, next_id = self._load_reservations()
				updated_reservations = []
				
				for reservation in reservations:
					reservation_time = reservation.get('time', '')
					reservation_id = reservation.get('id')
					last_executed_date = reservation.get('last_executed_date', '')
					
					# 시간이 일치하고 오늘 아직 실행하지 않았으면 실행
					if reservation_time == current_time_str and last_executed_date != current_date_str:
						command = reservation.get('command', '')
						if command and self.process_command_callback:
							print(f"⏰ 예약된 명령어 실행: [{reservation_id}] {command}")
							# 예약 실행 알림 전송
							await tel_send(f"⏰ 예약된 명령어 실행\n일련번호: [{reservation_id}]\n시간: {reservation_time}\n명령어: {command}")
							try:
								await self.process_command_callback(command)
							except Exception as e:
								print(f"❌ 예약 명령어 실행 중 오류: {e}")
								await tel_send(f"❌ 예약 명령어 [{reservation_id}] 실행 중 오류: {e}")
						
						# 마지막 실행 날짜 업데이트
						reservation['last_executed_date'] = current_date_str
					
					# 예약은 삭제하지 않고 유지 (매일 실행을 위해)
					updated_reservations.append(reservation)
				
				# 업데이트된 예약 목록 저장
				if updated_reservations != reservations:
					self._save_reservations(updated_reservations, next_id)
				
				# 1분마다 체크
				await asyncio.sleep(60)
			except asyncio.CancelledError:
				break
			except Exception as e:
				print(f"❌ 예약 체크 중 오류: {e}")
				await asyncio.sleep(60)
	
	def start(self):
		"""스케줄러 시작"""
		if self.is_running:
			return
		
		self.is_running = True
		self.task = asyncio.create_task(self._check_and_execute())
	
	def update_callback(self, process_command_callback):
		"""process_command_callback 업데이트"""
		self.process_command_callback = process_command_callback
	
	def stop(self):
		"""스케줄러 중지"""
		self.is_running = False
		if self.task and not self.task.done():
			self.task.cancel()
			self.task = None
	
	def add_reservation(self, time_str, command):
		"""예약 추가"""
		try:
			# 시간 형식 확인 (HH:MM)
			time_match = re.match(r'(\d{1,2}):(\d{2})', time_str)
			if not time_match:
				return None, "잘못된 시간 형식입니다. 예: 15:00"
			
			hour = int(time_match.group(1))
			minute = int(time_match.group(2))
			
			if not (0 <= hour <= 23 and 0 <= minute <= 59):
				return None, "시간 범위가 잘못되었습니다. (0-23:0-59)"
			
			reservations, next_id = self._load_reservations()
			
			# 새 예약 추가
			new_reservation = {
				'id': next_id,
				'time': f"{hour:02d}:{minute:02d}",
				'command': command,
				'created_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
			}
			
			reservations.append(new_reservation)
			
			if self._save_reservations(reservations, next_id + 1):
				return new_reservation['id'], None
			else:
				return None, "예약 저장에 실패했습니다."
		except Exception as e:
			return None, f"예약 추가 중 오류: {e}"
	
	def list_reservations(self):
		"""예약 목록 조회"""
		reservations, _ = self._load_reservations()
		return reservations
	
	def remove_reservation(self, reservation_id):
		"""예약 삭제"""
		try:
			reservation_id = int(reservation_id)
			reservations, next_id = self._load_reservations()
			
			# 해당 ID의 예약 찾기
			found = False
			for r in reservations:
				if r.get('id') == reservation_id:
					found = True
					break
			
			if not found:
				return False, "해당 일련번호의 예약을 찾을 수 없습니다."
			
			# 예약 제거
			reservations = [r for r in reservations if r.get('id') != reservation_id]
			
			if self._save_reservations(reservations, next_id):
				return True, None
			else:
				return False, "예약 삭제에 실패했습니다."
		except ValueError:
			return False, "일련번호는 숫자여야 합니다."
		except Exception as e:
			return False, f"예약 삭제 중 오류: {e}"

# 전역 스케줄러 인스턴스
_scheduler = None

def get_scheduler(script_dir, process_command_callback):
	"""스케줄러 인스턴스 가져오기 (싱글톤)"""
	global _scheduler
	if _scheduler is None:
		_scheduler = ReserveScheduler(script_dir, process_command_callback)
		_scheduler.start()
	return _scheduler

async def reserve_command(script_dir, process_command_callback, args):
	"""
	rsv 명령어를 처리합니다.
	
	사용법:
	- rsv {시간} {명령어} - 예약 추가
	- rsv list - 예약 목록 조회
	- rsv remove {일련번호} - 예약 삭제
	"""
	try:
		scheduler = get_scheduler(script_dir, process_command_callback)
		
		if not args or args.strip() == '':
			await tel_send("❌ 사용법: rsv {시간} {명령어}, rsv list, rsv remove {일련번호}")
			return False
		
		args_lower = args.strip().lower()
		
		# list 명령어
		if args_lower == 'list':
			reservations = scheduler.list_reservations()
			
			if not reservations:
				await tel_send("📋 예약된 명령어가 없습니다.")
				return True
			
			message = "📋 [예약된 명령어 목록]\n\n"
			for r in reservations:
				message += f"[{r.get('id')}] {r.get('time')} - {r.get('command')}\n"
				message += f"  생성: {r.get('created_at', '알 수 없음')}\n"
				last_executed = r.get('last_executed_date', '')
				if last_executed:
					message += f"  마지막 실행: {last_executed}\n"
				message += "\n"
			
			await tel_send(message.strip())
			return True
		
		# remove 명령어
		if args_lower.startswith('remove '):
			reservation_id = args_lower.replace('remove ', '').strip()
			success, error = scheduler.remove_reservation(reservation_id)
			
			if success:
				await tel_send(f"✅ 예약 [{reservation_id}]이 삭제되었습니다.")
				return True
			else:
				await tel_send(f"❌ {error}")
				return False
		
		# 예약 추가: rsv {시간} {명령어}
		# 시간 형식 찾기 (HH:MM)
		time_match = re.match(r'(\d{1,2}):(\d{2})', args.strip())
		if not time_match:
			await tel_send("❌ 잘못된 형식입니다. 예: rsv 15:00 cond 0 1 2")
			return False
		
		# 시간과 명령어 분리
		time_str = time_match.group(0)
		command = args.strip()[len(time_str):].strip()
		
		if not command:
			await tel_send("❌ 명령어를 입력해주세요. 예: rsv 15:00 cond 0 1 2")
			return False
		
		# 예약 추가
		reservation_id, error = scheduler.add_reservation(time_str, command)
		
		if reservation_id:
			await tel_send(f"✅ 예약이 추가되었습니다.\n일련번호: [{reservation_id}]\n시간: {time_str}\n명령어: {command}")
			return True
		else:
			await tel_send(f"❌ {error}")
			return False
		
	except Exception as e:
		await tel_send(f"❌ rsv 명령어 실행 중 오류: {e}")
		return False

