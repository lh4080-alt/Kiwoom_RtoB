import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from api.acc_val import fn_kt00004
from api.stk_rank import fn_ka00198
from api.stock_info import fn_ka10001
from api.check_bal import fn_kt00001
from api.check_unfilled import fn_ka10075
from utils.stock_code_normalizer import normalize_stock_code

async def report_command(token_manager, settings_manager=None, background_task_manager=None):
	"""report 명령어를 처리합니다 - 자금현황, 미체결, 보유종목, 선정종목 통합 조회"""
	try:
		async def _build_selected_stocks_message():
			"""
			골든크로스용 선정종목 메시지 생성
			- 종목명 + 코드만 표시
			"""
			if not (background_task_manager and hasattr(background_task_manager, 'selected_stocks') and background_task_manager.selected_stocks):
				return ""
			
			selected_raw = list(background_task_manager.selected_stocks)
			selected_norm = [normalize_stock_code(c) for c in selected_raw if c]
			selected_norm = [c for c in selected_norm if c]
			if not selected_norm:
				return ""
			
			# 우선 순위 조회(ka00198)에서 종목명 매칭 시도
			name_by_code = {}
			try:
				ranked_stocks = await asyncio.wait_for(
					fn_ka00198('N', '', token_manager.token),
					timeout=10.0
				)
				if ranked_stocks:
					for stock in ranked_stocks:
						stk_cd = normalize_stock_code(stock.get('stk_cd', ''))
						stk_nm = stock.get('stk_nm', '')
						if stk_cd and stk_nm and stk_cd in selected_norm:
							name_by_code[stk_cd] = stk_nm
			except Exception:
				# 실패해도 아래에서 개별 조회로 보완
				pass
			
			lines = ["👀 [골든크로스 선정 종목]\n"]
			for stk_cd in selected_norm:
				stock_name = name_by_code.get(stk_cd, '')
				if not stock_name:
					try:
						info = await fn_ka10001(stk_cd, token=token_manager.token)
						stock_name = info.get('stk_nm', '') if isinstance(info, dict) else str(info)
					except Exception:
						stock_name = "정보 조회 실패"
				lines.append(f"- {stock_name} ({stk_cd})")
			
			return "\n".join(lines).rstrip() + "\n"
		
		# 토큰이 없으면 새로 발급
		if not token_manager.token:
			token = await token_manager.get_token()
			if not token:
				await tel_send("❌ 토큰 발급에 실패했습니다")
				return False
		
		# ---------------------------------------------------------
		# 0. 👀 [골든크로스 선정 종목] - 있으면 가장 먼저 전송
		# ---------------------------------------------------------
		selected_message = await _build_selected_stocks_message()
		if selected_message:
			await tel_send(selected_message)
		
		# ---------------------------------------------------------
		# 1. 💰 [자금 현황] (예수금 상세 현황)
		# ---------------------------------------------------------
		balance_message = "💰 [자금 현황]\n\n"
		try:
			balance_data = await fn_kt00001(token=token_manager.token)
			
			# balance_data가 딕셔너리인지 확인
			if balance_data and isinstance(balance_data, dict):
				# D+0 예수금 (entr)
				entr_d0 = int(balance_data.get('entr', 0) or 0)
				
				# D+2 추정예수금 (d2_entra)
				d2_entra = int(balance_data.get('d2_entra', 0) or 0)
				
				# 매수 기준 금액 계산 (우선순위: d2_entra → ord_alowa → mny_ord_able_amt → ord_alow_amt → entr)
				calculatable_balance = int(balance_data.get('d2_entra', 0) or 0)
				if calculatable_balance <= 0:
					calculatable_balance = int(balance_data.get('ord_alowa', 0) or 0)
				if calculatable_balance <= 0:
					calculatable_balance = int(balance_data.get('mny_ord_able_amt', 0) or 0)
				if calculatable_balance <= 0:
					calculatable_balance = int(balance_data.get('ord_alow_amt', 0) or 0)
				if calculatable_balance <= 0:
					calculatable_balance = int(balance_data.get('entr', 0) or 0)
				
				balance_message += f"• 예수금(D+0): {entr_d0:,}원\n"
				balance_message += f"• 예수금(D+2): {d2_entra:,}원 (정산 예정 포함)\n"
				balance_message += f"• 주문가능액: {calculatable_balance:,}원 (⭐ 매수 기준)\n"
			else:
				balance_message += "   자금 정보를 가져올 수 없습니다.\n"
		except Exception as e:
			balance_message += f"   ⚠️ 자금 조회 실패: {e}\n"
		
		# 보유종목 평가 (총자산 계산용 — 아래 보유종목 섹션에서 재사용)
		account_data = None
		for _ in range(3):
			try:
				account_data = await asyncio.wait_for(
					fn_kt00004(False, 'N', '', token_manager.token),
					timeout=10.0
				)
				if account_data:
					break
			except Exception:
				await asyncio.sleep(1)

		if account_data:
			total_stock_evlt = 0
			for stk in account_data:
				evlt = int(stk.get('evlt_amt', 0) or 0)
				if evlt <= 0:
					evlt = int(stk.get('buy_amt', 0) or 0) + int(stk.get('pl_amt', 0) or 0)
				total_stock_evlt += evlt
			total_asset = d2_entra + total_stock_evlt
			balance_message += f"\n💎 총자산: {total_asset:,}원\n"
			balance_message += f"   (예수금 {d2_entra:,} + 주식 {total_stock_evlt:,})\n"

		balance_message += "\n" + "="*20 + "\n\n"

		# ---------------------------------------------------------
		# 2. ⏳ [미체결 내역]
		# ---------------------------------------------------------
		unfilled_codes = set()
		unfilled_message = "⏳ [미체결 내역]\n\n"
		try:
			unfilled_orders = await fn_ka10075(token=token_manager.token)
			
			if unfilled_orders:
				for order in unfilled_orders:
					# 미체결 내역 필드
					name = order.get('stk_nm', 'N/A')
					stock_code = normalize_stock_code(order.get('stk_cd', 'N/A'))
					if stock_code and stock_code != 'N/A':
						unfilled_codes.add(stock_code)
					# 로그에 있는 'ord_pric' 추가
					price = int(order.get('ord_pric', 0) or order.get('ord_prc', 0) or order.get('ord_uv', 0))
					# 로그에 있는 'oso_qty' 및 'ord_qty' 추가
					qty = int(order.get('oso_qty', 0) or order.get('rmn_qty', 0) or order.get('not_qty', 0))
					
					# 매수/매도 구분: io_tp_nm 필드를 우선 확인 (API 실제 응답값: "+매수", "-매도")
					io_type = order.get('io_tp_nm', '')
					if '매수' in io_type:
						side_str = '매수'
					elif '매도' in io_type:
						side_str = '매도'
					else:
						# io_tp_nm이 없는 경우 기존 ord_dv 로직을 예비로 사용
						side = order.get('ord_dv_nm', order.get('ord_dv', '매매'))
						if side in ['01', '1', '매수']:
							side_str = '매수'
						elif side in ['02', '2', '매도']:
							side_str = '매도'
						else:
							side_str = str(side)
					
					unfilled_message += f"   • {name} ({stock_code}) ({side_str})\n"
					unfilled_message += f"     {price:,}원 / {qty}주 대기\n"
			else:
				unfilled_message += "   대기 중인 주문이 없습니다.\n"
		except Exception as e:
			unfilled_message += f"   ⚠️ 미체결 조회 실패: {e}\n"
			
		unfilled_message += "\n" + "="*20 + "\n\n"
		
		# ---------------------------------------------------------
		# 3. (중간 톡) 자금현황 + 미체결 전송
		# ---------------------------------------------------------
		await tel_send(balance_message + unfilled_message)
		
		# ---------------------------------------------------------
		# 4. 💰 [보유 종목] - 항상 마지막에 전송
		# ---------------------------------------------------------
		if account_data is None:
			while account_data is None:
				try:
					account_data = await asyncio.wait_for(
						fn_kt00004(False, 'N', '', token_manager.token),
						timeout=10.0
					)
				except (asyncio.TimeoutError, Exception):
					await asyncio.sleep(1)
					continue

		held_message = "💰 [보유 종목]\n\n"
		if account_data:
			total_profit_loss = 0
			total_pl_amt = 0
			ordering_holdings_count = 0
			
			for stock in account_data:
				stock_code = normalize_stock_code(stock.get('stk_cd', 'N/A'))
				stock_name = stock.get('stk_nm', 'N/A')
				profit_loss_rate = float(stock.get('pl_rt', 0))
				pl_amt = int(stock.get('pl_amt', 0))
				remaining_qty = int(stock.get('rmnd_qty', 0))
				
				# 수익률에 따른 이모지 설정
				if profit_loss_rate > 0:
					emoji = "🔴"
				elif profit_loss_rate < 0:
					emoji = "🔵"
				else:
					emoji = "➡️"
				
				is_ordering = bool(stock_code) and (stock_code in unfilled_codes)
				if is_ordering:
					ordering_holdings_count += 1
				order_tag = " ⏳주문중" if is_ordering else ""
				
				held_message += f"{emoji} [{stock_name}] ({stock_code}){order_tag}\n"
				held_message += f"   수익률: {profit_loss_rate:+.2f}%\n"
				held_message += f"   평가손익: {pl_amt:,.0f}원\n"
				held_message += f"   보유수량: {remaining_qty:,}주\n\n"
				
				total_profit_loss += profit_loss_rate
				total_pl_amt += pl_amt
			
			# 보유 종목 요약
			avg_profit_loss = total_profit_loss / len(account_data) if account_data else 0
			held_message += f"📋 [보유 종목 요약]\n"
			held_message += f"   총 보유종목: {len(account_data)}개\n"
			held_message += f"   ⏳ 주문중: {ordering_holdings_count}개\n"
			held_message += f"   평균 수익률: {avg_profit_loss:+.2f}%\n"
			held_message += f"   총 평가손익: {total_pl_amt:,.0f}원\n"
		else:
			held_message += "   보유 종목이 없습니다.\n"
		
		held_message += "\n" + "="*20 + "\n\n"
		
		# ---------------------------------------------------------
		# 마지막 메시지 전송 (보유 종목이 항상 가장 마지막)
		# ---------------------------------------------------------
		await tel_send(held_message)
		
		return True
		
	except Exception as e:
		await tel_send(f"❌ report 명령어 실행 중 오류: {e}")
		return False

