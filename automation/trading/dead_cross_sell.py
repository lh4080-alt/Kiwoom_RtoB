"""
기능 4: 데드크로스 매도
단기봉이 장기봉보다 낮을 때 매도
"""
import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.acc_val import fn_kt00004
from api.sell_stock import fn_kt10001
from api.min_pole import fn_ka10080
from telegram.tel_send import tel_send
from utils.sold_stocks_manager import record_sold_stock
from utils.blocklist_checker import is_blocked, is_in_grid_trading
from utils.stock_code_normalizer import normalize_stock_code

async def check_and_sell_dead_cross(stk_cd, held_stock_codes, settings_manager, token):
	"""
	데드크로스를 확인하고 매도합니다.
	
	Args:
		stk_cd: 종목 코드
		held_stock_codes: 보유 종목 코드 리스트
		settings_manager: 설정 관리자
		token: API 토큰
	
	Returns:
		bool: 매도 성공 여부
	"""
	try:
		# 설정에서 분봉 값 가져오기
		chart_short = settings_manager.get_setting('chart_short', 5)
		chart_long = settings_manager.get_setting('chart_long', 20)
		
		# 단기봉과 장기봉 조회
		cur_prc_short = await fn_ka10080(stk_cd, chart_short, 'N', '', token)
		await asyncio.sleep(0.5)  # API 호출 간격
		
		cur_prc_long = await fn_ka10080(stk_cd, chart_long, 'N', '', token)
		await asyncio.sleep(0.5)
		
		if cur_prc_short == 0.0 or cur_prc_long == 0.0:
			return False
		
		# 데드크로스: 단기봉 < 장기봉
		if cur_prc_short < cur_prc_long:
			# 보유 종목이면 매도
			if stk_cd in held_stock_codes:
				# 그리드 트레이딩 종목 체크 (가장 먼저 확인)
				if is_in_grid_trading(stk_cd):
					print(f"{stk_cd}: 그리드 트레이딩 중인 종목이므로 자동매도를 건너뜁니다.")
					return False
				
				# 자동매도 금지 목록 체크
				if is_blocked(stk_cd):
					print(f"{stk_cd}: 자동매도 금지 목록에 있어 매도를 건너뜁니다.")
					return False
				return await _sell_stock(stk_cd, token)
		
		return False
		
	except Exception as e:
		print(f"데드크로스 체크 중 오류: {e}")
		return False

async def _sell_stock(stk_cd, token):
	"""종목 매도"""
	try:
		# 보유 수량 확인
		my_stocks = await fn_kt00004(False, 'N', '', token)
		
		if not my_stocks:
			return False
		
		for stock in my_stocks:
			if normalize_stock_code(stock['stk_cd']) == stk_cd:
				ord_qty = stock['rmnd_qty']
				profit_loss_rate = float(stock.get('pl_rt', 0))
				
				# 매도 주문
				result, _ = await fn_kt10001(stk_cd, str(ord_qty), 'N', '', token)
				
				if result == 0:
					# 매도 성공 시 기록
					record_sold_stock(stk_cd)
					
					# 수익률에 따른 이모지 설정
					if profit_loss_rate > 0:
						emoji = "🔴"
					elif profit_loss_rate < 0:
						emoji = "🔵"
					else:
						emoji = "➡️"
					
					await tel_send(f"{emoji} {stock['stk_nm']} ({stk_cd}) {ord_qty}주 매도 주문 (수익률: {profit_loss_rate:+.2f}%) [데드크로스]")
					return True
				break
		
		return False
		
	except Exception as e:
		print(f"매도 중 오류: {e}")
		await asyncio.sleep(1)
		return await _sell_stock(stk_cd, token)

