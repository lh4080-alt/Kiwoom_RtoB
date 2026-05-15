import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from telegram.commands.feature_parser import parse_feature_numbers

async def show_stop_menu(active_features):
	"""stop 명령어 메뉴 표시 (현재 실행 중인 기능 표시)"""
	if not active_features:
		await tel_send("⚠️ 현재 실행 중인 기능이 없습니다.")
		return
	
	features_desc = []
	if 1 in active_features:
		features_desc.append("1:조건식 검색 매수")
	if 2 in active_features:
		features_desc.append("2:수익율 매도")
	if 3 in active_features:
		features_desc.append("3:골든크로스 매수")
	if 4 in active_features:
		features_desc.append("4:데드크로스 매도")
	if 5 in active_features:
		features_desc.append("5:트레일링 스탑 매도")
	if 6 in active_features:
		features_desc.append("6:돌파 매수")
	if 7 in active_features:
		features_desc.append("7:그리드 트레이딩")
	
	active_str = ', '.join(features_desc)
	
	menu = f"""⏹️ 현재 실행 중인 기능:
{active_str}

사용법:
• stop - 현재 실행 중인 기능 목록 표시
• stop all - 모든 기능 중지
• stop 1 - 조건식 검색 매수만 중지
• stop 2 - 수익율 매도만 중지
• stop 14 - 조건식 검색 매수 + 데드크로스 매도 중지
• stop 234 - 수익율 매도 + 골든크로스 매수 + 데드크로스 매도 중지

예시: stop all, stop 14"""
	await tel_send(menu)

async def stop_command(websocket, settings_manager, background_task_manager, set_auto_start_false=True, feature_numbers=None):
	"""stop 명령어를 처리합니다."""
	try:
		# feature_numbers가 None이면 메뉴 표시 또는 모든 기능 중지
		if feature_numbers is None:
			if background_task_manager.is_running_any:
				await show_stop_menu(background_task_manager.active_features)
			else:
				await tel_send("⚠️ 현재 실행 중인 기능이 없습니다.")
			return True
		
		# 숫자 조합 파싱
		features = parse_feature_numbers(feature_numbers)
		
		if not features:
			await tel_send("❌ 잘못된 기능 번호입니다. 1, 2, 3, 4, 5, 6, 7, 8 중에서 선택해주세요.")
			return False
		
		# auto_start 설정 (사용자 명령일 때만 false로 설정)
		# 모든 기능을 중지하는 경우에만 auto_start를 false로 설정
		if set_auto_start_false:
			# 중지하려는 기능이 모든 활성 기능과 같으면 auto_start를 false로
			if set(features) == background_task_manager.active_features:
				if not settings_manager.update_setting('auto_start', False):
					await tel_send("❌ 설정 파일 업데이트 실패")
					return False
		
		# 선택된 기능들 중지
		success = await background_task_manager.stop_features(features)
		
		# 모든 기능이 중지되었는지 확인
		if not background_task_manager.is_running_any:
			if set_auto_start_false:
				await tel_send("✅ 모든 기능이 중지되었습니다")
		
		# stop 명령 실행 시 미체결 주문 취소 안내
		if success:
			await tel_send("💡 미체결 주문을 취소하려면 'ccu' 명령을 사용하세요.")
		
		return success
			
	except Exception as e:
		await tel_send(f"❌ stop 명령어 실행 중 오류: {e}")
		return False

