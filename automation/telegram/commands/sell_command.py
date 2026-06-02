import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from api.acc_val import fn_kt00004
from api.sell_stock import fn_kt10001, manual_sell_events
from api.stock_info import fn_ka10001
from utils.sold_stocks_manager import record_sold_stock
from utils.stock_code_normalizer import normalize_stock_code

async def sell_command(token_manager, stk_cd):
	"""sell 명령어를 처리합니다 - 보유 종목 중 선택한 종목을 매도"""
	try:
		# 토큰이 없으면 새로 발급
		if not token_manager.token:
			token = token_manager.get_token()
			if not token:
				await tel_send("❌ 토큰 발급에 실패했습니다")
				return False
		
		# 보유 종목 조회
		try:
			account_data = await asyncio.wait_for(
				fn_kt00004(False, 'N', '', token_manager.token),
				timeout=10.0
			)
		except asyncio.TimeoutError:
			await tel_send("⏰ 서버로부터 응답이 늦어지고 있습니다. 나중에 다시 시도해주세요.")
			return False
		
		# 보유 종목이 없으면 종료
		if not account_data:
			await tel_send("❌ 보유 종목이 없습니다.")
			return False
		
		# 종목코드로 보유 종목 찾기 (stk_cd 정규화 후 비교)
		target_stock = None
		for stock in account_data:
			stock_code = normalize_stock_code(stock.get('stk_cd', ''))
			# 대소문자를 모두 대문자로 변환하여 비교 (case-insensitive)
			if stock_code.upper() == stk_cd.upper():
				target_stock = stock
				break
		
		# 보유 종목이 아니면 종료
		if not target_stock:
			await tel_send(f"❌ 종목코드 {stk_cd}는 보유 종목이 아닙니다.")
			return False
		
		# 보유 수량 확인
		remaining_qty = int(target_stock.get('rmnd_qty', 0))
		if remaining_qty <= 0:
			await tel_send(f"❌ 보유 수량이 0주입니다.")
			return False
		
		# 매도 전 수익율 정보 저장
		pl_rt = float(target_stock.get('pl_rt', 0))
		
		# 종목명 조회
		try:
			stock_info_result = await asyncio.wait_for(
				fn_ka10001(stk_cd, 'N', '', token_manager.token),
				timeout=5.0
			)
			stock_name = stock_info_result.get('stk_nm', stk_cd) if isinstance(stock_info_result, dict) else stock_info_result
		except Exception:
			stock_name = target_stock.get('stk_nm', stk_cd)
		
		# 매도 주문 실행 (전체 수량 매도)
		try:
			# 익절/손절과 동일하게 종목코드 정규화
			stock_code_for_api = normalize_stock_code(target_stock.get('stk_cd', stk_cd))
			sell_result, order_no = await asyncio.wait_for(
				fn_kt10001(stock_code_for_api, str(remaining_qty), 'N', '', token_manager.token),
				timeout=10.0
			)
			
			if sell_result == 0 or sell_result == '0':
				# 정합성: 매도 주문 성공 시 holdings.json에서도 즉시 제거
				# (안 하면 0B push에서 touch_executor가 유령 손절 시도 + trade_log 오염)
				try:
					from utils.holdings import load_holdings, remove_holding
					from utils.touch_trade_log import update_exit
					holdings = await load_holdings()
					touch_h = next((h for h in holdings
					                if h.get('code') == stock_code_for_api
					                and h.get('source') == 'touch'), None)
					# trade_log: source='touch' holdings면 update_exit('manual') — remove 전에 호출 (ord_no 확보)
					if touch_h:
						cur_price = float(target_stock.get('cur_prc', 0) or 0)
						if cur_price <= 0:
							cur_price = float(target_stock.get('pur_pric', 0) or 0)
						asyncio.create_task(update_exit(
							code=stock_code_for_api,
							ord_no=str(touch_h.get('ord_no', '')),
							exit_price=cur_price,
							exit_reason='manual',
						))
					# source 무관 holdings 제거 (잔고-봇 정합성)
					await remove_holding(stock_code_for_api)
				except Exception:
					pass  # 정합성 fix 실패해도 매도는 계속

				# 주문번호가 있는 경우 체결 대기
				if order_no:
					# 이벤트 생성 및 등록
					wait_event = asyncio.Event()
					manual_sell_events[order_no] = wait_event
					
					try:
						# 체결 대기 안내 메시지
						await tel_send("매도 주문이 접수되었습니다. 체결을 기다립니다...")
						
						# 체결 신호 대기 (60초 타임아웃)
						try:
							await asyncio.wait_for(wait_event.wait(), timeout=60.0)
							
							# 체결 완료 메시지
							# 수익율에 따른 이모지 및 손익 여부 설정
							if pl_rt > 0:
								result_emoji = "🔴"
								result_type = "익절"
							elif pl_rt < 0:
								result_emoji = "🔵"
								result_type = "손절"
							else:
								result_emoji = "➡️"
								result_type = "손익없음"
							
							message = f"{result_emoji} [{stock_name}] ({stk_cd}) {remaining_qty}주 매도 체결 완료\n수익율: {pl_rt:+.2f}% ({result_type})"
							await tel_send(message)
							
							# 매도 성공 시 기록
							record_sold_stock(stock_code_for_api)
							return True
							
						except asyncio.TimeoutError:
							# 타임아웃 발생
							await tel_send("⏳ 매도 주문은 들어갔으나 체결 확인 시간이 초과되었습니다. MTS/HTS를 확인해주세요.")
							return False
							
					finally:
						# 이벤트 정리 (메모리 누수 방지)
						if order_no in manual_sell_events:
							del manual_sell_events[order_no]
				else:
					# 주문번호가 없는 경우 기존 로직 유지 (즉시 성공 메시지)
					# 매도 성공 시 기록
					record_sold_stock(stock_code_for_api)
					
					# 수익율에 따른 이모지 및 손익 여부 설정
					if pl_rt > 0:
						result_emoji = "🔴"
						result_type = "익절"
					elif pl_rt < 0:
						result_emoji = "🔵"
						result_type = "손절"
					else:
						result_emoji = "➡️"
						result_type = "손익없음"
					
					message = f"{result_emoji} [{stock_name}] ({stk_cd}) {remaining_qty}주 매도 완료\n수익율: {pl_rt:+.2f}% ({result_type})"
					await tel_send(message)
					return True
			else:
				await tel_send(f"❌ [{stock_name}] ({stk_cd}) 매도 주문 실패 (오류 코드: {sell_result})")
				return False
		except asyncio.TimeoutError:
			await tel_send("⏰ 매도 주문 처리 중 시간 초과가 발생했습니다.")
			return False
		except Exception as e:
			await tel_send(f"❌ 매도 주문 중 오류 발생: {e}")
			return False
		
	except Exception as e:
		await tel_send(f"❌ sell 명령어 실행 중 오류: {e}")
		return False

