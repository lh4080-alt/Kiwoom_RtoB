import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from utils.market_hour import MarketHour

async def condition_command(token_manager, settings_manager, websocket, background_task_manager, start_callback, stop_callback, numbers=None):
	"""condition 명령어를 처리합니다 - 조건식 목록 조회 또는 search_seq 설정"""
	try:
		# cond list 처리
		if numbers == 'list':
			# settings.json에서 저장된 조건식 번호 가져오기
			search_seq = settings_manager.get_setting('search_seq', [])
			if not isinstance(search_seq, list):
				if isinstance(search_seq, str):
					search_seq = [s.strip() for s in search_seq.replace(',', ' ').split() if s.strip()]
				else:
					search_seq = [str(search_seq)] if search_seq else []
			
			if not search_seq:
				await tel_send("📋 등록된 조건식이 없습니다.")
				return True
			
			# 웹소켓이 연결되어 있지 않으면 오류
			if not websocket or not websocket.connected:
				await tel_send("❌ 웹소켓이 연결되어 있지 않습니다. 잠시 후 다시 시도해주세요.")
				return False
			
			# 조건식 목록 가져오기 (타임아웃 10초)
			try:
				condition_data = await websocket.get_condition_list(timeout=10.0)
			except Exception as e:
				await tel_send(f"❌ 조건식 목록 조회 중 오류: {e}")
				return False
			
			if not condition_data:
				await tel_send("❌ 조건식 목록을 가져올 수 없습니다.")
				return False
			
			# 조건식 번호를 키로 하는 딕셔너리 생성
			condition_dict = {}
			for condition in condition_data:
				if len(condition) >= 2:
					condition_dict[str(condition[0])] = condition[1]
			
			# 저장된 조건식 번호와 이름 출력
			message = "📋 [등록된 조건식 목록]\n\n"
			for seq in search_seq:
				seq_str = str(seq)
				condition_name = condition_dict.get(seq_str, '알 수 없음')
				message += f"• {seq_str}번: {condition_name}\n"
			
			await tel_send(message)
			return True
		
		# cond clear 처리
		if numbers == 'clear':
			# 기능 1이 실행 중인지 확인
			was_running = 1 in background_task_manager.active_features
			
			# search_seq를 빈 리스트로 설정
			if settings_manager.update_setting('search_seq', []):
				await tel_send("✅ 등록된 조건식 목록이 모두 삭제되었습니다.")
				
				# 기능 1이 실행 중이면 조건식 업데이트
				if was_running:
					await websocket.update_conditions([])
					await tel_send("✅ 조건식이 변경되었습니다.")
				
				return True
			else:
				await tel_send("❌ 조건식 목록 삭제에 실패했습니다.")
				return False
		
		# 조건검색식 조회 안내 메시지 전송
		await tel_send("조건검색식 기능들이 제대로 동작하지 않으면 이 링크를 확인해보세요.\nhttps://yalco.notion.site/2e2ff6b3a357809bac49c966cbc6c7f6?pvs=73")
		
		# 숫자가 제공된 경우 search_seq 설정
		if numbers is not None:
			try:
				# numbers가 문자열인 경우 공백으로 분리하여 리스트로 변환
				if isinstance(numbers, str):
					seq_list = [s.strip() for s in numbers.split() if s.strip()]
				elif isinstance(numbers, list):
					seq_list = [str(n) for n in numbers]
				else:
					# 단일 숫자인 경우 리스트로 변환
					seq_list = [str(numbers)]
				
				if not seq_list:
					await tel_send("❌ 조건식 번호를 입력해주세요. 예: cond 1 2 4")
					return False
				
				# 기능 1이 실행 중인지 확인
				was_running = 1 in background_task_manager.active_features
				
				# 리스트로 저장
				if settings_manager.update_setting('search_seq', seq_list):
					seq_str = ", ".join(seq_list)
					await tel_send(f"✅ 검색 조건식이 {seq_str}번으로 설정되었습니다")
					
					# 기능 1이 실행 중이면 조건식 업데이트
					if was_running:
						await websocket.update_conditions(seq_list)
						await tel_send("✅ 조건식이 변경되었습니다")
					
					return True
				else:
					await tel_send("❌ 검색 조건식 설정에 실패했습니다")
					return False
			except ValueError:
				await tel_send("❌ 잘못된 숫자 형식입니다. 예: cond 1 2 4")
				return False
		
		# 숫자가 제공되지 않은 경우 조건식 목록 조회
		# 웹소켓이 연결되어 있지 않으면 오류
		if not websocket or not websocket.connected:
			await tel_send("❌ 웹소켓이 연결되어 있지 않습니다. 잠시 후 다시 시도해주세요.")
			return False
		
		# 조건식 목록 가져오기 (타임아웃 10초)
		try:
			condition_data = await websocket.get_condition_list(timeout=10.0)
		except Exception as e:
			await tel_send(f"❌ 조건식 목록 조회 중 오류: {e}")
			return False
		
		if not condition_data:
			await tel_send("📋 조건식 목록이 없습니다.")
			return False
		
		# 조건식 목록 포맷팅 (번호를 숫자로 변환한 뒤 숫자 기준 정렬)
		def _condition_sort_key(condition):
			try:
				return int(condition[0])
			except Exception:
				# 예상치 못한 형식이면 뒤로 보내기
				return float('inf')
		
		condition_data = sorted(condition_data, key=_condition_sort_key)

		message = "📋 [조건식 목록]\n\n"
		
		for condition in condition_data:
			condition_id = condition[0] if len(condition) > 0 else 'N/A'
			condition_name = condition[1] if len(condition) > 1 else 'N/A'
			message += f"• {condition_id}: {condition_name}\n"
		
		message += "\n💡 사용법: cond {번호들} (예: cond 1 2 4)"
		await tel_send(message)
		return True
		
	except Exception as e:
		await tel_send(f"❌ cond 명령어 실행 중 오류: {e}")
		return False

