import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.acc_val import fn_kt00004 as get_my_stocks
from api.sell_stock import fn_kt10001 as sell_stock
from api.stock_info import fn_ka10001 as stock_info
from telegram.tel_send import tel_send
from utils.get_setting import cached_setting
from utils.sold_stocks_manager import record_sold_stock
from utils.blocklist_checker import is_blocked, is_in_grid_trading
from utils.stock_code_normalizer import normalize_stock_code

# 주의: 이 모듈의 함수들은 더 이상 사용되지 않습니다.
# 기능 5(트레일링 스탑)는 이제 웹소켓 기반으로 처리됩니다 (automation/realtime/websocket.py).
# 고점 관리 로직은 Portfolio Manager로 통합되었습니다.

# 종목별 최고가 추적 딕셔너리 (더 이상 사용되지 않음)
stock_highs = {}

async def trailing_stop_sell(token=None):
	"""
	트레일링 스탑 매도 로직 (더 이상 사용되지 않음)
	- 보유종목의 현재가를 확인하고 고점을 추적
	- 고점에서 지정된 퍼센티지가 떨어지면 매도
	- 이제 웹소켓 기반으로 처리됩니다.
	"""
	# 트레일링 스탑 퍼센티지 설정
	trailing_stop_rate = cached_setting('trailing_stop_rate', 3.0)
	
	try:
		# 보유 종목 조회
		my_stocks = await get_my_stocks(token=token)
		if not my_stocks:
			return True
		
		# 각 종목에 대해 현재가 확인 및 트레일링 스탑 체크
		for stock in my_stocks:
			stk_cd_clean = normalize_stock_code(stock['stk_cd'])
			rmnd_qty = int(stock.get('rmnd_qty', 0))
			
			# 보유 수량이 0이면 스킵
			if rmnd_qty <= 0:
				continue
			
			try:
				# 현재가 조회
				stock_info_result = await stock_info(stk_cd_clean, token=token)
				cur_prc = stock_info_result.get('cur_prc', 0.0) if isinstance(stock_info_result, dict) else 0.0
				stock_name = stock_info_result.get('stk_nm', stk_cd_clean) if isinstance(stock_info_result, dict) else stk_cd_clean
				
				if cur_prc <= 0:
					print(f"{stk_cd_clean}: 현재가 조회 실패 또는 유효하지 않은 가격")
					continue
				
				# 최고가 초기화 또는 갱신
				if stk_cd_clean not in stock_highs:
					# 처음 조회하는 종목이면 현재가를 최고가로 설정
					stock_highs[stk_cd_clean] = cur_prc
					print(f"{stock_name} ({stk_cd_clean}): 최고가 초기화 {cur_prc:,.0f}원")
				elif cur_prc > stock_highs[stk_cd_clean]:
					# 현재가가 기존 최고가보다 높으면 최고가 갱신
					stock_highs[stk_cd_clean] = cur_prc
					print(f"{stock_name} ({stk_cd_clean}): 최고가 갱신 {cur_prc:,.0f}원")
				
				# 트레일링 스탑 체크
				high_price = stock_highs[stk_cd_clean]
				drop_percentage = ((high_price - cur_prc) / high_price) * 100
				
				if drop_percentage >= trailing_stop_rate:
					# 그리드 트레이딩 종목 체크 (가장 먼저 확인)
					if is_in_grid_trading(stk_cd_clean):
						print(f"{stock_name} ({stk_cd_clean}): 그리드 트레이딩 중인 종목이므로 자동매도를 건너뜁니다.")
						continue
					
					# 자동매도 금지 목록 체크
					if is_blocked(stk_cd_clean):
						print(f"{stock_name} ({stk_cd_clean}): 자동매도 금지 목록에 있어 매도를 건너뜁니다.")
						continue
					
					# 고점에서 지정된 퍼센티지 이상 하락했으므로 매도
					print(f"{stock_name} ({stk_cd_clean}): 트레일링 스탑 매도 조건 충족 (고점: {high_price:,.0f}원, 현재가: {cur_prc:,.0f}원, 하락률: {drop_percentage:.2f}%)")
					
					# 매도 주문 실행
					sell_result, _ = await sell_stock(stk_cd_clean, str(rmnd_qty), token=token)
					if sell_result != 0:
						print(f"{stock_name} ({stk_cd_clean}): 매도 실패 (오류 코드: {sell_result})")
						continue
					
					# 매도 성공 시 기록
					record_sold_stock(stk_cd_clean)
					
					# 최고가 딕셔너리에서 제거
					if stk_cd_clean in stock_highs:
						del stock_highs[stk_cd_clean]
					
					# 익절/손절 판단 (수익률 확인)
					pl_rt = float(stock.get('pl_rt', 0))
					result_type = "익절" if pl_rt > 0 else "손절"
					result_emoji = "🔴" if pl_rt > 0 else "🔵"
					
					# 텔레그램 보고
					message = f"{result_emoji} [{stock_name}] ({stk_cd_clean}) {rmnd_qty}주 매도 완료\n고점: {high_price:,.0f}원 → 현재가: {cur_prc:,.0f}원 (하락률: {drop_percentage:.2f}%)\n수익률: {pl_rt:.2f}% [{result_type}]\n[트레일링 매도]"
					await tel_send(message)
					print(message)
				
			except Exception as e:
				error_msg = str(e)
				# stk_acnt_evlt_prst가 포함된 경우 출력하지 않음
				if 'stk_acnt_evlt_prst' not in error_msg:
					print(f"{stk_cd_clean}: 트레일링 스탑 체크 중 오류 발생: {error_msg}")
				continue
		
		return True
		
	except Exception as e:
		error_msg = str(e)
		# stk_acnt_evlt_prst가 포함된 경우 출력하지 않음
		if 'stk_acnt_evlt_prst' not in error_msg:
			print(f"오류 발생(trailing_stop_sell): {error_msg}")
		return False

def reset_stock_highs():
	"""보유 종목이 변경되었을 때 최고가 딕셔너리 초기화 (더 이상 사용되지 않음)"""
	stock_highs.clear()

if __name__ == "__main__":
	from api.login import fn_au10001 as get_token
	import asyncio
	asyncio.run(trailing_stop_sell(token=get_token()))

