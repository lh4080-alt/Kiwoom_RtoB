import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from api.acc_val import fn_kt00004
from api.sell_stock import fn_kt10001
from api.stock_info import fn_ka10001
from api.check_unfilled import fn_ka10075
from utils.sold_stocks_manager import record_sold_stock
from utils.stock_code_normalizer import normalize_stock_code

async def sell_all_command(token_manager):
	"""sellall 명령어를 처리합니다 - 보유 중인 모든 종목을 시장가로 매도"""
	try:
		# 토큰이 없으면 새로 발급
		if not token_manager.token:
			token = await token_manager.get_token()
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
		
		# 미체결 매도 주문 조회 (이미 주문이 들어간 종목 제외하기 위해)
		unfilled_sell_orders = []
		try:
			unfilled_orders = await asyncio.wait_for(
				fn_ka10075(trde_tp='1', token=token_manager.token),  # 매도 주문만 조회
				timeout=10.0
			)
			if unfilled_orders:
				if isinstance(unfilled_orders, list):
					unfilled_sell_orders = unfilled_orders
				elif isinstance(unfilled_orders, dict):
					unfilled_sell_orders = [unfilled_orders]
		except Exception as e:
			print(f"미체결 주문 조회 중 오류 (계속 진행): {e}")
			# 미체결 조회 실패해도 계속 진행
		
		# 미체결 매도 주문이 있는 종목코드 집합 생성
		unfilled_stock_codes = set()
		for order in unfilled_sell_orders:
			if isinstance(order, dict):
				order_stk_cd = normalize_stock_code(order.get('stk_cd', ''))
				if order_stk_cd:
					unfilled_stock_codes.add(order_stk_cd)
		
		# 보유 수량이 있고 미체결 매도 주문이 없는 종목만 필터링
		stocks_to_sell = []
		skipped_stocks = []  # 미체결 주문이 있어서 건너뛴 종목
		for stock in account_data:
			remaining_qty = int(stock.get('rmnd_qty', 0))
			if remaining_qty > 0:
				stock_code = normalize_stock_code(stock.get('stk_cd', ''))
				if stock_code in unfilled_stock_codes:
					# 이미 미체결 매도 주문이 있는 종목은 건너뛰기
					stock_name = stock.get('stk_nm', stock_code)
					skipped_stocks.append({
						'name': stock_name,
						'code': stock_code,
						'qty': remaining_qty
					})
				else:
					stocks_to_sell.append(stock)
		
		if not stocks_to_sell:
			if skipped_stocks:
				skip_message = "❌ 매도할 종목이 없습니다.\n\n"
				skip_message += "⏸️ [이미 매도 주문 중인 종목]\n"
				for skipped in skipped_stocks:
					skip_message += f"  - [{skipped['name']}] ({skipped['code']}) {skipped['qty']:,}주\n"
				await tel_send(skip_message)
			else:
				await tel_send("❌ 매도할 종목이 없습니다. (보유 수량이 0주인 종목만 있습니다)")
			return False
		
		# 건너뛴 종목이 있으면 알림
		if skipped_stocks:
			skip_message = "⏸️ [이미 매도 주문 중인 종목은 제외합니다]\n"
			for skipped in skipped_stocks:
				skip_message += f"  - [{skipped['name']}] ({skipped['code']}) {skipped['qty']:,}주\n"
			skip_message += "\n"
			await tel_send(skip_message)
		
		# 시작 메시지 전송
		total_stocks = len(stocks_to_sell)
		await tel_send(f"📊 전체 매도 시작: 총 {total_stocks}개 종목\n각 종목 사이에 1초 대기합니다...")
		
		# 매도 결과 추적
		success_count = 0
		fail_count = 0
		success_details = []
		fail_details = []
		total_profit_rate = 0.0
		total_profit_amount = 0
		
		# 각 종목에 대해 순차적으로 매도
		for idx, stock in enumerate(stocks_to_sell, 1):
			try:
				stock_code = normalize_stock_code(stock.get('stk_cd', ''))
				remaining_qty = int(stock.get('rmnd_qty', 0))
				pl_rt = float(stock.get('pl_rt', 0))
				pl_amt = int(stock.get('pl_amt', 0))
				stock_name = stock.get('stk_nm', stock_code)
				
				# 종목명 조회 시도
				try:
					stock_info_result = await asyncio.wait_for(
						fn_ka10001(stock_code, 'N', '', token_manager.token),
						timeout=5.0
					)
					if isinstance(stock_info_result, dict):
						stock_name = stock_info_result.get('stk_nm', stock_name)
				except Exception:
					pass  # 기본 종목명 사용
				
				# 매도 주문 실행
				try:
					sell_result, _ = await asyncio.wait_for(
						fn_kt10001(stock_code, str(remaining_qty), 'N', '', token_manager.token),
						timeout=10.0
					)
					
					if sell_result == 0:
						# 매도 주문 접수 완료 (실제 체결 여부는 확인하지 않음)
						record_sold_stock(stock_code)
						success_count += 1
						total_profit_rate += pl_rt
						total_profit_amount += pl_amt
						
						# 수익율에 따른 이모지 설정
						if pl_rt > 0:
							result_emoji = "🔴"
						elif pl_rt < 0:
							result_emoji = "🔵"
						else:
							result_emoji = "➡️"
						
						# 주문 접수 완료 메시지 전송
						await tel_send(
							f"{result_emoji} 📝 [{stock_name}] ({stock_code}) 매도 주문 접수 완료\n"
							f"   수량: {remaining_qty:,}주\n"
							f"   수익률: {pl_rt:+.2f}%"
						)
						
						success_details.append({
							'name': stock_name,
							'code': stock_code,
							'qty': remaining_qty,
							'pl_rt': pl_rt,
							'emoji': result_emoji
						})
					else:
						# 매도 실패
						fail_count += 1
						fail_details.append({
							'name': stock_name,
							'code': stock_code,
							'error': f"오류 코드: {sell_result}"
						})
						
				except asyncio.TimeoutError:
					fail_count += 1
					fail_details.append({
						'name': stock_name,
						'code': stock_code,
						'error': "시간 초과"
					})
				except Exception as e:
					fail_count += 1
					fail_details.append({
						'name': stock_name,
						'code': stock_code,
						'error': str(e)
					})
				
				# 마지막 종목이 아니면 1초 대기
				if idx < total_stocks:
					await asyncio.sleep(1)
					
			except Exception as e:
				fail_count += 1
				stock_code = normalize_stock_code(stock.get('stk_cd', ''))
				stock_name = stock.get('stk_nm', stock_code)
				fail_details.append({
					'name': stock_name,
					'code': stock_code,
					'error': f"처리 중 오류: {e}"
				})
		
		# 최종 결과 메시지 생성
		result_message = f"📊 [전체 매도 주문 접수 완료]\n\n"
		result_message += f"✅ 주문 접수: {success_count}개\n"
		if fail_count > 0:
			result_message += f"❌ 주문 실패: {fail_count}개\n"
		result_message += "\n"
		
		# 성공한 종목 상세 정보
		if success_details:
			result_message += "✅ [매도 주문 접수 완료 종목]\n"
			for detail in success_details:
				result_message += f"{detail['emoji']} [{detail['name']}] ({detail['code']})\n"
				result_message += f"   수량: {detail['qty']:,}주, 수익률: {detail['pl_rt']:+.2f}%"
				if 'note' in detail:
					result_message += detail['note']
				result_message += "\n"
			result_message += "\n"
		
		# 실패한 종목 상세 정보
		if fail_details:
			result_message += "❌ [매도 실패 종목]\n"
			for detail in fail_details:
				result_message += f"[{detail['name']}] ({detail['code']})\n"
				result_message += f"   오류: {detail['error']}\n"
			result_message += "\n"
		
		# 전체 요약
		if success_count > 0:
			avg_profit_rate = total_profit_rate / success_count
			result_message += f"📈 [전체 요약]\n"
			result_message += f"   평균 수익률: {avg_profit_rate:+.2f}%\n"
			result_message += f"   총 평가손익: {total_profit_amount:,.0f}원\n"
		
		await tel_send(result_message)
		return True
		
	except Exception as e:
		await tel_send(f"❌ sellall 명령어 실행 중 오류: {e}")
		return False

