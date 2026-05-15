import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.check_bal import fn_kt00001 as get_balance
from api.check_bid import fn_ka10004 as check_bid
from api.buy_stock import fn_kt10000 as buy_stock
from api.stock_info import fn_ka10001 as stock_info
from api.acc_val import fn_kt00004 as get_my_stocks
from api.check_unfilled import fn_ka10075 as check_unfilled
from telegram.tel_send import tel_send
from utils.get_setting import get_setting
from api.login import fn_au10001 as get_token
from utils.sold_stocks_manager import is_in_cooldown, get_cooldown_remaining
from utils.stock_code_normalizer import normalize_stock_code
from utils.blocklist_checker import is_blocked

def _to_int(value, default: int = 0) -> int:
	"""API 응답 값(문자열/숫자/None)을 안전하게 int로 변환."""
	try:
		if value is None:
			return default
		if isinstance(value, bool):
			return int(value)
		if isinstance(value, (int, float)):
			return int(value)
		s = str(value).replace(',', '').strip()
		if not s:
			return default
		return int(s)
	except Exception:
		try:
			s = str(value).replace(',', '').strip()
			return int(float(s)) if s else default
		except Exception:
			return default

async def chk_n_buy(stk_cd, token=None, condition_name=None, seq_id=None):

	try:
		my_stocks = await get_my_stocks(token=token)
		for stock in my_stocks:
			if normalize_stock_code(stock['stk_cd']) == stk_cd:
				print("이미 보유 중입니다.")
				return
		
		# 보유종목 개수 제한 체크
		max_holdings = get_setting('max_holdings', 0)
		if max_holdings > 0 and len(my_stocks) >= max_holdings:
			print(f"보유종목 개수 제한에 도달했습니다. (현재: {len(my_stocks)}개, 제한: {max_holdings}개)")
			return
	except Exception as e:
		print("보유종목 조회 중 오류 발생:", e)
		return

	# 자동매매 금지 목록 체크
	if is_blocked(stk_cd):
		print(f"[매수 차단] {stk_cd} 종목은 자동매매 금지 목록에 등록되어 있습니다.")
		return

	# 미체결 체크 (중복 매수 방지)
	try:
		# ka10075는 종목코드로 미체결 조회 지원 (해당 종목에 미체결이 있으면 중복 주문 방지)
		unfilled_orders = await check_unfilled(stk_cd=stk_cd, trde_tp='2', token=token)
		if unfilled_orders:
			# 혹시 모를 포맷 차이를 대비해 리스트/딕셔너리 모두 처리
			if isinstance(unfilled_orders, dict):
				unfilled_orders = [unfilled_orders]

			# 종목코드 정규화 후 매칭 (API에 따라 'A' prefix가 붙을 수 있음)
			target_cd = normalize_stock_code(stk_cd)
			for order in unfilled_orders:
				if not isinstance(order, dict):
					continue
				order_cd = normalize_stock_code(order.get('stk_cd') or order.get('pdno') or '')
				if (not order_cd) or (order_cd == target_cd):
					order_no = order.get('orgn_ord_no') or order.get('ord_no') or order.get('odno')
					print(f"미체결 주문이 존재합니다. (주문번호: {order_no}) - 중복 매수 방지")
					return
	except Exception as e:
		# 안전을 위해 미체결 조회 실패 시 매수를 중단
		print("미체결 조회 중 오류 발생:", e)
		return

	# 쿨다운 체크
	cooldown_hours = get_setting('sell_cooldown_hours', 24)
	if is_in_cooldown(stk_cd, cooldown_hours):
		remaining = get_cooldown_remaining(stk_cd, cooldown_hours)
		print(f"매도 후 쿨다운 중입니다. {remaining:.1f}시간 후에 매수 가능합니다.")
		return

	try:
		balance_data = await get_balance(token=token)
		# D+2 예수금 우선 적용: d2_entra (1순위) → ord_alowa (2순위) → mny_ord_able_amt (3순위) → ord_alow_amt (4순위) → entr (5순위)
		if isinstance(balance_data, dict):
			# 1. D+2 추정예수금 (매도 대금 포함, 최우선 적용)
			balance = _to_int(balance_data.get('d2_entra', 0))
			
			# 2. 잔고가 0이거나 없으면 주문가능금액(ord_alowa) 확인
			if balance <= 0:
				balance = _to_int(balance_data.get('ord_alowa', 0))
			
			# 3. 대체 필드 확인 (mny_ord_able_amt)
			if balance <= 0:
				balance = _to_int(balance_data.get('mny_ord_able_amt', 0))
			
			# 4. 대체 필드 확인 (ord_alow_amt)
			if balance <= 0:
				balance = _to_int(balance_data.get('ord_alow_amt', 0))
			
			# 5. 최후의 수단: D+0 예수금 (entr)
			if balance <= 0:
				balance = _to_int(balance_data.get('entr', 0))
		else:
			balance = _to_int(balance_data)
		if balance <= 0:
			print("잔고가 없습니다.")
			return
	except Exception as e:
		print("잔고 조회 중 오류 발생:", e)
		return

	# 매수 모드 확인
	buy_mode = get_setting('buy_mode', 'ratio')
	buy_fixed_amount = get_setting('buy_fixed_amount', 100000)
	
	if buy_mode == 'fixed_strict':
		# 고정 금액 엄격 모드(bftx): 설정액 전액 가능할 때만 매수
		expense = float(buy_fixed_amount)
		if balance < expense:
			shortfall = int(expense - balance)
			msg = (
				f"⚠️ [bftx] 잔고 부족으로 매수 취소\n"
				f"설정 금액: {int(expense):,}원 | 잔고: {balance:,}원 | 부족: {shortfall:,}원"
			)
			print(msg)
			await tel_send(msg)
			return
		# fixed_strict는 잔고 초과 시 금액 깎지 않고 위에서 이미 return
	elif buy_mode == 'fixed':
		# 고정 금액 모드(유연)
		expense = float(buy_fixed_amount)
	else:
		# 비율 모드 (기본값)
		buy_ratio = get_setting('buy_ratio', 5.0) / 100
		expense = balance * buy_ratio
	
	# 예수금을 초과하지 않도록 제한 (fixed_strict는 위에서 검증 완료)
	if buy_mode != 'fixed_strict' and expense > balance:
		expense = balance
	
	print('지출할 금액:', expense)

	try:
		bid = int(await check_bid(stk_cd, token=token))
	except Exception as e:
		print("호가 조회 중 오류 발생:", e)
		return

	if bid > 0:
		ord_qty = int(expense // bid)  # 내림하여 정수로 변환
		if ord_qty == 0:
			print("주문할 주식 수량이 0입니다.")
			return
		print('주문할 주식 수량:', ord_qty)

	try:
		ret_code, order_no = await buy_stock(stk_cd, ord_qty, bid, token=token)
		if ret_code != 0:
			print("주문 실패")
			return
	except Exception as e:
		print("주문 중 오류 발생:", e)
		return

	try:
		stock_info_result = await stock_info(stk_cd, token=token)
		stock_name = stock_info_result.get('stk_nm', stk_cd) if isinstance(stock_info_result, dict) else stock_info_result
	except Exception as e:
		print("종목정보 조회 중 오류 발생:", e)
		stock_name = stk_cd

	# 텔레그램 메시지 구성 (seq 번호 포함)
	message = f'{stock_name} {ord_qty}주 매수 주문'
	if seq_id is not None:
		message += f' (조건식: {seq_id})'
	print(message)
	await tel_send(message)

if __name__ == '__main__':
	chk_n_buy('005930', token=get_token())

