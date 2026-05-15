"""
기능 3: 골든크로스 매수
단기봉이 장기봉보다 높을 때 매수
"""
import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.check_bal import fn_kt00001
from api.buy_stock import fn_kt10000
from api.min_pole import fn_ka10080
from telegram.tel_send import tel_send
from utils.sold_stocks_manager import is_in_cooldown

def _to_float(value, default: float = 0.0) -> float:
	"""API 응답 값(문자열/숫자/None)을 안전하게 float로 변환."""
	try:
		if value is None:
			return default
		if isinstance(value, bool):
			return float(int(value))
		if isinstance(value, (int, float)):
			return float(value)
		s = str(value).replace(',', '').strip()
		if not s:
			return default
		return float(s)
	except Exception:
		return default

async def check_and_buy_golden_cross(stk_cd, selected_stocks, held_stock_codes, settings_manager, token):
	"""
	골든크로스를 확인하고 매수합니다.
	
	Args:
		stk_cd: 종목 코드
		selected_stocks: 선정된 종목 리스트
		held_stock_codes: 보유 종목 코드 리스트
		settings_manager: 설정 관리자
		token: API 토큰
	
	Returns:
		bool: 매수 성공 여부
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
		
		# 골든크로스: 단기봉 > 장기봉
		if cur_prc_short > cur_prc_long:
			# 선정된 종목이고 보유 종목이 아니면 매수
			if stk_cd in selected_stocks and stk_cd not in held_stock_codes:
				# 보유종목 개수 제한 체크
				max_holdings = settings_manager.get_setting('max_holdings', 0)
				if max_holdings > 0:
					current_holdings = len(held_stock_codes)
					if current_holdings >= max_holdings:
						print(f"보유종목 개수 제한에 도달했습니다. (현재: {current_holdings}개, 제한: {max_holdings}개)")
						return False
				
				# 쿨다운 체크
				cooldown_hours = settings_manager.get_setting('sell_cooldown_hours', 24)
				if is_in_cooldown(stk_cd, cooldown_hours):
					print(f"{stk_cd}는 매도 후 쿨다운 중입니다. 매수를 건너뜁니다.")
					return False
				
				return await _buy_stock(stk_cd, cur_prc_short, settings_manager, token)
		
		return False
		
	except Exception as e:
		print(f"골든크로스 체크 중 오류: {e}")
		return False

async def _buy_stock(stk_cd, current_price, settings_manager, token):
	"""종목 매수"""
	try:
		# 예수금 조회
		balance_data = await fn_kt00001('N', '', token)
		# D+2 예수금 우선 적용: d2_entra (1순위) → ord_alowa (2순위) → mny_ord_able_amt (3순위) → ord_alow_amt (4순위) → entr (5순위)
		if isinstance(balance_data, dict):
			# 1. D+2 추정예수금 (매도 대금 포함, 최우선 적용)
			entry_amount = _to_float(balance_data.get('d2_entra', 0), 0.0)
			
			# 2. 잔고가 0이거나 없으면 주문가능금액(ord_alowa) 확인
			if entry_amount <= 0:
				entry_amount = _to_float(balance_data.get('ord_alowa', 0), 0.0)
			
			# 3. 대체 필드 확인 (mny_ord_able_amt)
			if entry_amount <= 0:
				entry_amount = _to_float(balance_data.get('mny_ord_able_amt', 0), 0.0)
			
			# 4. 대체 필드 확인 (ord_alow_amt)
			if entry_amount <= 0:
				entry_amount = _to_float(balance_data.get('ord_alow_amt', 0), 0.0)
			
			# 5. 최후의 수단: D+0 예수금 (entr)
			if entry_amount <= 0:
				entry_amount = _to_float(balance_data.get('entr', 0), 0.0)
		else:
			entry_amount = _to_float(balance_data, 0.0)
		if entry_amount <= 0:
			return False
		
		# 매수 모드 확인
		buy_mode = settings_manager.get_setting('buy_mode', 'ratio')
		buy_fixed_amount = settings_manager.get_setting('buy_fixed_amount', 100000)
		
		if buy_mode == 'fixed_strict':
			# 고정 금액 엄격 모드: 설정액 전액 가능할 때만 매수
			if entry_amount < buy_fixed_amount:
				return False
			buy_amount = float(buy_fixed_amount)
		elif buy_mode == 'fixed':
			# 고정 금액 모드 (유연)
			buy_amount = float(buy_fixed_amount)
		else:
			# 비율 모드 (기본값)
			buy_ratio = settings_manager.get_setting('buy_ratio', 5.0)
			buy_amount = entry_amount * (buy_ratio / 100.0)
		
		# 예수금을 초과하지 않도록 제한 (fixed_strict는 위에서 검증 완료)
		if buy_mode != 'fixed_strict' and buy_amount > entry_amount:
			buy_amount = entry_amount
		
		# 수량 계산 (현재가 기준)
		ord_qty = int(buy_amount / current_price)
		if ord_qty <= 0:
			return False
		
		# 매수 주문
		result = await fn_kt10000(stk_cd, str(ord_qty), str(int(current_price)), 'N', '', token)
		
		if result == 0:
			await tel_send(f"🟢 {stk_cd} {ord_qty}주 매수 주문 (가격: {int(current_price)}원) [골든크로스]")
			return True
		
		return False
		
	except Exception as e:
		print(f"매수 중 오류: {e}")
		return False

