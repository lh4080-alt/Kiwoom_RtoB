import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.acc_val import fn_kt00004 as get_my_stocks
from api.sell_stock import fn_kt10001 as sell_stock
from telegram.tel_send import tel_send
from utils.get_setting import cached_setting
from api.login import fn_au10001 as get_token
from utils.sold_stocks_manager import record_sold_stock
from utils.blocklist_checker import is_blocked, is_in_grid_trading, is_in_wave_trading
from utils.stock_code_normalizer import normalize_stock_code

# 주의: 이 함수는 더 이상 사용되지 않습니다.
# 기능 2(익절/손절)는 이제 웹소켓 기반으로 처리됩니다 (automation/realtime/websocket.py).
# 기존 폴링 방식에서 웹소켓 이벤트 기반으로 전환되었습니다.
async def chk_n_sell(token=None):

	# 익절 수익율(%) - 목표 수익율에 도달하면 매도
	TP_RATE = cached_setting('take_profit_rate', 10.0)
	# 손절 수익율(%) - 손실 한계에 도달하면 매도
	SL_RATE = cached_setting('stop_loss_rate', -10.0)

	try:
		my_stocks = await get_my_stocks(token=token)
		if not my_stocks:
			print("보유 종목이 없습니다.")
			return True
			
		for stock in my_stocks:

			# pl_rt는 문자열이므로 float으로 변환하여 비교해야 함
			pl_rt = float(stock['pl_rt'])
			if pl_rt > TP_RATE or pl_rt < SL_RATE:
				stk_cd_clean = normalize_stock_code(stock['stk_cd'])
				
				# 그리드 트레이딩 종목 체크 (가장 먼저 확인)
				if is_in_grid_trading(stk_cd_clean):
					print(f"{stock['stk_nm']} ({stk_cd_clean}): 그리드 트레이딩 중인 종목이므로 자동매도를 건너뜁니다.")
					continue
				
				# 분할 트레이딩(Wave) 종목 체크
				if is_in_wave_trading(stk_cd_clean):
					print(f"{stock['stk_nm']} ({stk_cd_clean}): 분할 트레이딩(Wave) 중인 종목이므로 자동매도를 건너뜁니다.")
					continue
				
				# 자동매도 금지 목록 체크
				if is_blocked(stk_cd_clean):
					print(f"{stock['stk_nm']} ({stk_cd_clean}): 자동매도 금지 목록에 있어 매도를 건너뜁니다.")
					continue
				
				sell_result, _ = await sell_stock(stk_cd_clean, stock['rmnd_qty'], token=token)
				if sell_result != 0:
					print("매도 실패")
					return True

				# 매도 성공 시 기록
				record_sold_stock(stk_cd_clean)

				result_type = "익절" if pl_rt > TP_RATE else "손절"
				result_emoji = "🔴" if pl_rt > TP_RATE else "🔵"
				message = f'{result_emoji} {stock["stk_nm"]} ({stk_cd_clean}) {int(stock["rmnd_qty"])}주 매도 완료 (수익율: {pl_rt}%) [{result_type}]'
				await tel_send(message)
				print(message)

		return True  # 성공적으로 실행됨

	except Exception as e:
		error_msg = str(e)
		# stk_acnt_evlt_prst가 포함된 경우 출력하지 않음
		if 'stk_acnt_evlt_prst' not in error_msg:
			print(f"오류 발생(chk_n_sell): {error_msg}")
		return False  # 예외 발생으로 실패

if __name__ == "__main__":
	chk_n_sell(token=get_token())

