import sys
import os
import asyncio

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send

async def fcond_add_command(settings_manager, args, websocket=None):
	"""
	fcond add 명령어를 처리합니다.
	
	Args:
		settings_manager: SettingsManager 인스턴스
		args: 명령어 인자 리스트 (예: ['3', 'wave', 'add', '()', '300000'])
	
	Returns:
		bool: 성공 여부
	"""
	try:
		if not args or len(args) < 2:
			await tel_send("❌ 사용법: fcond add {조건식인덱스} {명령어템플릿}\n예: fcond add 3 wave add () 300000")
			return False
		
		# 조건식 인덱스 추출 및 숫자 검증
		cond_index = str(args[0]).strip()
		if not cond_index.isdigit():
			await tel_send("❌ 조건식 인덱스는 숫자만 입력 가능합니다.\n예: fcond add 3 wave add () 300000")
			return False
		
		# 나머지 인자들을 명령어 템플릿으로 조합
		command_template = ' '.join(args[1:]).strip()
		
		# 유효성 검사: 명령어 템플릿에 ()가 포함되어 있어야 함
		if '()' not in command_template:
			await tel_send("❌ 명령어 템플릿에 반드시 '()' 문자열이 포함되어 있어야 합니다.\n예: fcond add 3 wave add () 300000")
			return False
		
		# 기존 fcond_rules 가져오기
		fcond_rules = settings_manager.get_setting('fcond_rules', {})
		if not isinstance(fcond_rules, dict):
			fcond_rules = {}
		
		# 규칙 추가
		fcond_rules[cond_index] = command_template
		
		# 설정 저장
		if settings_manager.update_setting('fcond_rules', fcond_rules):
			# 텔레그램 메시지에서 ()를 {검색된 종목 코드}로 변환
			display_command = command_template.replace('()', '{검색된 종목 코드}')
			await tel_send(f"✅ fcond 규칙이 추가되었습니다.\n조건식 인덱스: {cond_index}\n명령어: {display_command}")
			
			# 웹소켓을 통해 서버로 감시 요청 전송
			if websocket and websocket.connected:
				# 이미 등록되어 있는지 확인
				cond_index_str = str(cond_index).strip()
				if cond_index_str not in websocket.registered_seqs:
					# CNSRLST 선행 호출 보장
					await websocket._ensure_condition_list_loaded()
					
					# CNSRREQ 패킷 전송
					await websocket.send_message({
						'trnm': 'CNSRREQ',
						'seq': cond_index_str,
						'search_type': '1',
						'stex_tp': 'K',
					}, websocket.token)
					
					# 등록된 번호에 추가
					websocket.registered_seqs.add(cond_index_str)
					print(f'[FCOND] 조건식 등록 요청 전송: seq {cond_index_str}')
				else:
					print(f'[FCOND] 조건식 {cond_index_str}는 이미 등록되어 있습니다.')
			else:
				print(f'[FCOND] 웹소켓이 연결되지 않아 서버 등록을 건너뜁니다. (기능 시작 시 자동 등록됩니다)')
			
			return True
		else:
			await tel_send("❌ fcond 규칙 추가에 실패했습니다.")
			return False
		
	except Exception as e:
		await tel_send(f"❌ fcond add 명령어 실행 중 오류: {e}")
		return False

async def fcond_list_command(settings_manager):
	"""
	fcond list 명령어를 처리합니다.
	
	Args:
		settings_manager: SettingsManager 인스턴스
	
	Returns:
		bool: 성공 여부
	"""
	try:
		# fcond_rules 가져오기
		fcond_rules = settings_manager.get_setting('fcond_rules', {})
		if not isinstance(fcond_rules, dict):
			fcond_rules = {}
		
		if not fcond_rules:
			await tel_send("📋 등록된 fcond 규칙이 없습니다.")
			return True
		
		# 규칙 목록 포맷팅
		message = "📋 [fcond 규칙 목록]\n\n"
		for index, command in sorted(fcond_rules.items()):
			# 텔레그램 메시지에서 ()를 {검색된 종목 코드}로 변환
			display_command = command.replace('()', '{검색된 종목 코드}')
			message += f"[{index}] : {display_command}\n"
		
		await tel_send(message)
		return True
		
	except Exception as e:
		await tel_send(f"❌ fcond list 명령어 실행 중 오류: {e}")
		return False

async def fcond_remove_command(settings_manager, args, websocket=None):
	"""
	fcond remove 명령어를 처리합니다.
	
	Args:
		settings_manager: SettingsManager 인스턴스
		args: 명령어 인자 리스트 (예: ['3'])
		websocket: UnifiedWebSocket 인스턴스 (선택)
	
	Returns:
		bool: 성공 여부
	"""
	try:
		if not args or len(args) < 1:
			await tel_send("❌ 사용법: fcond remove {조건식인덱스}\n예: fcond remove 3")
			return False
		
		# 조건식 인덱스 추출
		cond_index = str(args[0]).strip()
		
		# 기존 fcond_rules 가져오기
		fcond_rules = settings_manager.get_setting('fcond_rules', {})
		if not isinstance(fcond_rules, dict):
			fcond_rules = {}
		
		# 규칙이 존재하는지 확인
		if cond_index not in fcond_rules:
			await tel_send(f"❌ 조건식 인덱스 {cond_index}에 대한 규칙이 없습니다.")
			return False
		
		# 규칙 삭제
		removed_command = fcond_rules.pop(cond_index)
		
		# 설정 저장
		if settings_manager.update_setting('fcond_rules', fcond_rules):
			await tel_send(f"✅ fcond 규칙이 삭제되었습니다.\n조건식 인덱스: {cond_index}\n삭제된 명령어: {removed_command}")
			
			# 웹소켓을 통해 서버로 해제 요청 전송 (search_seq에 없고 fcond에만 있었던 경우)
			if websocket and websocket.connected:
				cond_index_str = str(cond_index).strip()
				
				# search_seq에 포함되어 있는지 확인
				from utils.get_setting import get_setting
				search_seq_value = get_setting('search_seq', '0')
				
				# search_seq를 리스트로 변환
				if isinstance(search_seq_value, list):
					search_seq_list = [str(s).strip() for s in search_seq_value if s]
				else:
					if isinstance(search_seq_value, str):
						search_seq_list = [s.strip() for s in search_seq_value.replace(',', ' ').split() if s.strip()]
					else:
						search_seq_list = [str(search_seq_value)] if search_seq_value else []
				
				# search_seq에 없고 fcond에만 있었던 경우에만 해제
				if cond_index_str not in search_seq_list and cond_index_str in websocket.registered_seqs:
					# CNSRCLR 패킷 전송
					await websocket.send_message({
						'trnm': 'CNSRCLR',
						'seq': cond_index_str,
					}, websocket.token)
					
					# 등록된 번호에서 제거
					websocket.registered_seqs.discard(cond_index_str)
					print(f'[FCOND] 조건식 해제 요청 전송: seq {cond_index_str}')
				elif cond_index_str in search_seq_list:
					print(f'[FCOND] 조건식 {cond_index_str}는 search_seq에도 포함되어 있어 해제하지 않습니다.')
				else:
					print(f'[FCOND] 조건식 {cond_index_str}는 등록되어 있지 않습니다.')
			
			return True
		else:
			await tel_send("❌ fcond 규칙 삭제에 실패했습니다.")
			return False
		
	except Exception as e:
		await tel_send(f"❌ fcond remove 명령어 실행 중 오류: {e}")
		return False

def _format_cooldown_time(hours):
	"""
	시간을 친절한 형식으로 변환합니다.
	
	Args:
		hours: 시간 (float)
	
	Returns:
		str: 포맷된 시간 문자열
	"""
	if hours == 0:
		return "비활성화"
	
	whole_hours = int(hours)
	minutes = int((hours - whole_hours) * 60)
	
	if whole_hours == 0:
		return f"{minutes}분"
	elif minutes == 0:
		return f"{whole_hours}시간"
	else:
		return f"{whole_hours}시간 {minutes}분"

async def fcond_cooldown_command(settings_manager, args):
	"""
	fcond cooldown 명령어를 처리합니다.
	
	Args:
		settings_manager: SettingsManager 인스턴스
		args: 명령어 인자 리스트 (예: ['6'])
	
	Returns:
		bool: 성공 여부
	"""
	try:
		if not args or len(args) < 1:
			# 현재 cooldown 시간 조회
			current_cooldown = settings_manager.get_setting('fcond_cooldown_hours', 0)
			formatted_time = _format_cooldown_time(current_cooldown)
			
			message = "📋 [fcond cooldown 설정]\n\n"
			message += f"현재 설정: {formatted_time}\n\n"
			message += "💡 fcond 명령어 실행 후 지정된 시간 동안\n"
			message += "같은 종목에 대한 같은 명령어 재실행이 방지됩니다.\n\n"
			message += "설정 변경: fcond cooldown {시간}\n"
			message += "예: fcond cooldown 6 (6시간)\n"
			message += "예: fcond cooldown 0.5 (30분)\n"
			message += "예: fcond cooldown 0 (비활성화)"
			
			await tel_send(message)
			return True
		
		# cooldown 시간 설정
		try:
			cooldown_hours = float(args[0])
			if cooldown_hours < 0:
				await tel_send("❌ cooldown 시간은 0 이상이어야 합니다.\n\n사용법: fcond cooldown {시간}\n예: fcond cooldown 6 또는 fcond cooldown 0.5")
				return False
			
			if settings_manager.update_setting('fcond_cooldown_hours', cooldown_hours):
				formatted_time = _format_cooldown_time(cooldown_hours)
				
				message = "✅ [fcond cooldown 설정 완료]\n\n"
				message += f"설정된 시간: {formatted_time}\n"
				message += f"원본 값: {cooldown_hours}시간\n\n"
				
				if cooldown_hours == 0:
					message += "💡 cooldown이 비활성화되었습니다.\n"
					message += "fcond 명령어는 cooldown 없이 즉시 실행됩니다."
				else:
					message += "💡 이제 fcond 명령어 실행 후 지정된 시간 동안\n"
					message += "같은 종목에 대한 같은 명령어 재실행이 방지됩니다."
				
				await tel_send(message)
				return True
			else:
				await tel_send("❌ fcond cooldown 설정에 실패했습니다.")
				return False
		except ValueError:
			await tel_send("❌ cooldown 시간은 숫자여야 합니다.\n\n사용법: fcond cooldown {시간}\n예: fcond cooldown 6 (6시간)\n예: fcond cooldown 0.5 (30분)")
			return False
		
	except Exception as e:
		await tel_send(f"❌ fcond cooldown 명령어 실행 중 오류: {e}")
		return False

async def fcond_command(settings_manager, args, websocket=None):
	"""
	fcond 명령어를 처리합니다 (라우터 역할).
	
	Args:
		settings_manager: SettingsManager 인스턴스
		args: 명령어 인자 리스트
		websocket: UnifiedWebSocket 인스턴스 (선택)
	
	Returns:
		bool: 성공 여부
	"""
	try:
		if not args or len(args) == 0:
			# 인자가 없으면 사용법 안내
			help_message = "📋 [fcond 명령어 사용법]\n\n"
			help_message += "• fcond add {조건식인덱스} {명령어템플릿}\n"
			help_message += "  예: fcond add 3 wave add () 300000\n\n"
			help_message += "• fcond list\n"
			help_message += "  등록된 fcond 규칙 목록 조회\n\n"
			help_message += "• fcond remove {조건식인덱스}\n"
			help_message += "  예: fcond remove 3\n\n"
			help_message += "• fcond cooldown {시간}\n"
			help_message += "  fcond 명령어 실행 후 지정된 시간 동안 같은 명령어 재실행 방지\n"
			help_message += "  예: fcond cooldown 6 (6시간)\n"
			help_message += "  예: fcond cooldown (현재 설정 조회)\n\n"
			help_message += "💡 명령어 템플릿에는 반드시 '()'가 포함되어야 하며, 조건식 편입 시 종목코드로 치환됩니다."
			await tel_send(help_message)
			return True
		
		subcommand = args[0].lower()
		
		if subcommand == 'add':
			return await fcond_add_command(settings_manager, args[1:], websocket)
		elif subcommand == 'list':
			return await fcond_list_command(settings_manager)
		elif subcommand == 'remove':
			return await fcond_remove_command(settings_manager, args[1:], websocket)
		elif subcommand == 'cooldown':
			return await fcond_cooldown_command(settings_manager, args[1:])
		else:
			await tel_send(f"❌ 알 수 없는 fcond 하위 명령어입니다: {subcommand}\n사용 가능한 명령어: add, list, remove, cooldown")
			return False
		
	except Exception as e:
		await tel_send(f"❌ fcond 명령어 실행 중 오류: {e}")
		return False
