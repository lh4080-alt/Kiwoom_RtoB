import sys
import os
import asyncio

# 상위 경로 설정 (기존 파일 참조)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from api.buy_stock import fn_kt10000  # 매수 주문 함수
from api.stock_info import get_stock_info  # 현재가 조회 함수
from api.check_bal import fn_kt00001 as get_balance
from utils.get_setting import get_setting  # 설정값 조회

def _to_int(value, default: int = 0) -> int:
	"""API 응답 값을 안전하게 int로 변환."""
	try:
		if value is None:
			return default
		if isinstance(value, (int, float)):
			return int(value)
		s = str(value).replace(',', '').strip()
		return int(s) if s else default
	except Exception:
		return default

async def buy_command(token_manager, args):
	"""
	buy 명령어를 처리합니다 - 수동 매수
	
	Args:
		token_manager: TokenManager 인스턴스
		args: 명령어 인자 리스트
			- Case 1: ['005930'] - 설정된 1회 매수 금액만큼 현재가로 매수
			- Case 2: ['005930', '10'] - 지정한 수량만큼 현재가로 매수
			- Case 3: ['005930', '10', '68000'] - 지정한 수량과 지정가로 매수
	
	Returns:
		bool: 성공 여부
	"""
	try:
		# 유효성 검사: args 길이는 1개 이상이어야 함
		if not args or len(args) == 0:
			await tel_send("❌ 사용법: buy <종목코드> [수량] [가격]")
			return False
		
		# 종목코드 추출 및 검증
		stock_code = args[0].strip()
		
		# 종목코드가 6자리 숫자인지 확인
		if not stock_code.isdigit() or len(stock_code) != 6:
			await tel_send(f"❌ 종목코드는 6자리 숫자여야 합니다. (입력: {stock_code})")
			return False
		
		# 토큰 발급
		token = await token_manager.get_token()
		if not token:
			await tel_send("❌ 토큰 발급에 실패했습니다")
			return False
		
		# 현재가 조회 (필수)
		stock_info = await get_stock_info(stock_code, token)
		if not stock_info:
			await tel_send(f"❌ 종목 정보 조회에 실패했습니다. (종목코드: {stock_code})")
			return False
		
		# 종목명 추출
		stock_name = stock_info.get('stk_nm', stock_code)
		
		# 현재가 추출 및 변환
		cur_prc_raw = stock_info.get('cur_prc', '0')
		# cur_prc가 문자열일 수 있으므로 float로 변환
		try:
			if isinstance(cur_prc_raw, str):
				# 음수로 오는 경우가 있으므로 절댓값 처리
				if cur_prc_raw.startswith('-'):
					cur_prc_raw = cur_prc_raw[1:]
				cur_prc = float(cur_prc_raw) if cur_prc_raw else 0.0
			else:
				cur_prc = float(cur_prc_raw) if cur_prc_raw else 0.0
		except (ValueError, TypeError):
			cur_prc = 0.0
		
		if cur_prc <= 0:
			await tel_send(f"❌ 현재가 조회에 실패했습니다. (종목코드: {stock_code})")
			return False
		
		# 케이스별 변수 설정 (qty, price)
		if len(args) == 1:
			# Case 1: buy 005930 (인자 1개)
			buy_mode = get_setting('buy_mode', 'ratio')
			buy_amount = float(get_setting('buy_fixed_amount', 100000))
			price = cur_prc
			# fixed_strict: 잔고가 설정 금액 이상일 때만 매수
			if buy_mode == 'fixed_strict':
				balance_data = await get_balance(token=token)
				if isinstance(balance_data, dict):
					balance = _to_int(balance_data.get('d2_entra', 0))
					if balance <= 0:
						balance = _to_int(balance_data.get('ord_alowa', 0))
					if balance <= 0:
						balance = _to_int(balance_data.get('mny_ord_able_amt', 0))
					if balance <= 0:
						balance = _to_int(balance_data.get('ord_alow_amt', 0))
					if balance <= 0:
						balance = _to_int(balance_data.get('entr', 0))
				else:
					balance = _to_int(balance_data)
				if balance < buy_amount:
					shortfall = int(buy_amount - balance)
					await tel_send(
						f"⚠️ [bftx] 잔고 부족으로 매수 취소\n"
						f"설정 금액: {int(buy_amount):,}원 | 잔고: {balance:,}원 | 부족: {shortfall:,}원"
					)
					return False
			# 수량: 설정 금액 // 현재가
			qty = int(buy_amount // cur_prc)
			if qty == 0:
				if buy_mode == 'fixed_strict':
					await tel_send("❌ 계산된 매수 수량이 0입니다. (설정 금액이 현재가보다 작음)")
					return False
				qty = 1  # 비엄격 모드: 최소 1주
		
		elif len(args) == 2:
			# Case 2: buy 005930 10 (인자 2개)
			try:
				qty = int(args[1])
				if qty <= 0:
					await tel_send("❌ 수량은 1 이상이어야 합니다.")
					return False
			except (ValueError, TypeError):
				await tel_send(f"❌ 수량은 숫자여야 합니다. (입력: {args[1]})")
				return False
			price = cur_prc
		
		elif len(args) >= 3:
			# Case 3: buy 005930 10 68000 (인자 3개 이상)
			try:
				qty = int(args[1])
				if qty <= 0:
					await tel_send("❌ 수량은 1 이상이어야 합니다.")
					return False
			except (ValueError, TypeError):
				await tel_send(f"❌ 수량은 숫자여야 합니다. (입력: {args[1]})")
				return False
			
			try:
				price = int(args[2])
				if price <= 0:
					await tel_send("❌ 가격은 1 이상이어야 합니다.")
					return False
			except (ValueError, TypeError):
				await tel_send(f"❌ 가격은 숫자여야 합니다. (입력: {args[2]})")
				return False
		
		else:
			await tel_send("❌ 사용법: buy <종목코드> [수량] [가격]")
			return False
		
		# 매수 주문 실행 (fn_kt10000 호출)
		result = await fn_kt10000(
			stk_cd=stock_code,    # 종목코드
			ord_qty=str(qty),     # 수량 (문자열 변환)
			ord_uv=str(price),    # 단가 (문자열 변환)
			token=token,          # 토큰
			order_type='limit'    # 수동 매수는 '지정가'로 고정
		)
		
		# 반환값 처리: (return_code, order_no) 튜플
		return_code, order_no = result
		
		# 결과 메시지 전송
		if return_code == 0 or order_no:
			# 성공 시
			message = f"✅ [수동매수] {stock_name}({stock_code}) 주문 전송\n수량: {qty}주 / 가격: {int(price):,}원\n'report' 명령어로 주문 결과를 확인해주세요!"
			await tel_send(message)
			return True
		else:
			# 실패 시 에러 코드 분석 (문자열로 변환하여 비교 권장)
			str_code = str(return_code).strip()
			
			if str_code == '20':
				error_msg = f"❌ 주문 실패: 주문 가격 오류 (Code: {return_code})\n주문 가격이 상/하한가를 벗어났거나 호가 단위가 맞지 않습니다. 현재가를 확인해주세요."
			else:
				error_msg = f"❌ 주문 실패 (에러 코드: {return_code})"
			
			await tel_send(error_msg)
			return False
		
	except Exception as e:
		await tel_send(f"❌ buy 명령어 실행 중 오류: {e}")
		return False

