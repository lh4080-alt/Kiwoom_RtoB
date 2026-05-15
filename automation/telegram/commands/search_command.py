import asyncio
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from api.stock_info import get_stock_info
from telegram.commands.token_manager import TokenManager

async def search_command(token_manager, stock_code):
	"""
	srch 명령어를 처리합니다 - 종목 정보 조회
	
	Args:
		token_manager: TokenManager 인스턴스
		stock_code: 종목코드 (6자리 문자열)
	
	Returns:
		bool: 성공 여부
	"""
	try:
		# 종목코드 유효성 검증 (6자리 숫자)
		if not stock_code or len(stock_code) != 6 or not stock_code.isdigit():
			await tel_send("❌ 종목코드는 6자리 숫자여야 합니다. (예: srch 005930)")
			return False
		
		# 토큰 확인 및 발급
		if not token_manager.token:
			token = await token_manager.get_token()
			if not token:
				await tel_send("❌ 토큰 발급에 실패했습니다.")
				return False
		else:
			token = token_manager.token
		
		# 종목 정보 조회
		try:
			stock_data = await asyncio.wait_for(
				get_stock_info(stock_code, token),
				timeout=10.0
			)
		except asyncio.TimeoutError:
			await tel_send("⏰ 서버로부터 응답이 늦어지고 있습니다. 나중에 다시 시도해주세요.")
			return False
		
		if not stock_data:
			await tel_send(f"❌ 종목코드 {stock_code}의 정보를 조회할 수 없습니다.")
			return False
		
		# 데이터 추출 및 포맷팅
		stk_nm = stock_data.get('stk_nm', stock_code)
		cur_prc_raw = stock_data.get('cur_prc', '0')
		flu_rt_raw = stock_data.get('flu_rt', '0')
		pred_pre_raw = stock_data.get('pred_pre', '0')
		trde_qty_raw = stock_data.get('trde_qty', '0')
		high_pric_raw = stock_data.get('high_pric', '0')
		low_pric_raw = stock_data.get('low_pric', '0')
		open_pric_raw = stock_data.get('open_pric', '0')
		
		# 현재가 처리 (음수 제거 및 숫자 변환)
		if isinstance(cur_prc_raw, str) and cur_prc_raw.startswith('-'):
			cur_prc_str = cur_prc_raw[1:]
		else:
			cur_prc_str = str(cur_prc_raw)
		
		try:
			cur_prc = float(cur_prc_str) if cur_prc_str else 0.0
		except (ValueError, TypeError):
			cur_prc = 0.0
		
		# 등락률 처리 (이미 부호 포함되어 있을 수 있음)
		flu_rt_str = str(flu_rt_raw) if flu_rt_raw else '0'
		try:
			flu_rt = float(flu_rt_str) if flu_rt_str else 0.0
		except (ValueError, TypeError):
			flu_rt = 0.0
		
		# 전일 대비 처리
		pred_pre_str = str(pred_pre_raw) if pred_pre_raw else '0'
		if isinstance(pred_pre_str, str) and pred_pre_str.startswith('-'):
			pred_pre_abs = pred_pre_str[1:]
		else:
			pred_pre_abs = pred_pre_str
		try:
			pred_pre = float(pred_pre_abs) if pred_pre_abs else 0.0
			# 원래 부호 유지
			if pred_pre_str.startswith('-'):
				pred_pre = -pred_pre
		except (ValueError, TypeError):
			pred_pre = 0.0
		
		# 거래량 처리
		trde_qty_str = str(trde_qty_raw) if trde_qty_raw else '0'
		try:
			trde_qty = int(float(trde_qty_str)) if trde_qty_str else 0
		except (ValueError, TypeError):
			trde_qty = 0
		
		# 고가 처리
		high_pric_str = str(high_pric_raw) if high_pric_raw else '0'
		if isinstance(high_pric_str, str) and high_pric_str.startswith('-'):
			high_pric_str = high_pric_str[1:]
		try:
			high_pric = float(high_pric_str) if high_pric_str else 0.0
		except (ValueError, TypeError):
			high_pric = 0.0
		
		# 저가 처리
		low_pric_str = str(low_pric_raw) if low_pric_raw else '0'
		if isinstance(low_pric_str, str) and low_pric_str.startswith('-'):
			low_pric_str = low_pric_str[1:]
		try:
			low_pric = float(low_pric_str) if low_pric_str else 0.0
		except (ValueError, TypeError):
			low_pric = 0.0
		
		# 시가 처리
		open_pric_str = str(open_pric_raw) if open_pric_raw else '0'
		if isinstance(open_pric_str, str) and open_pric_str.startswith('-'):
			open_pric_str = open_pric_str[1:]
		try:
			open_pric = float(open_pric_str) if open_pric_str else 0.0
		except (ValueError, TypeError):
			open_pric = 0.0
		
		# 메시지 포맷팅
		message = f"[{stk_nm} ({stock_code})]\n"
		
		# 현재가 및 등락률 (부호에 따라 이모지 추가)
		if flu_rt > 0:
			flu_emoji = "📈"
			flu_sign = "+"
		elif flu_rt < 0:
			flu_emoji = "📉"
			flu_sign = ""
		else:
			flu_emoji = "➡️"
			flu_sign = ""
		
		message += f"현재가: {int(cur_prc):,}원 ({flu_sign}{flu_rt:.1f}%)\n"
		
		# 거래량
		message += f"거래량: {trde_qty:,}\n"
		
		# 시가, 고가, 저가
		message += f"시가: {int(open_pric):,} | 고가: {int(high_pric):,} | 저가: {int(low_pric):,}"
		
		# 전일 대비가 있으면 추가 정보로 표시
		if pred_pre != 0:
			pred_sign = "+" if pred_pre > 0 else ""
			message += f"\n전일 대비: {pred_sign}{int(pred_pre):,}원"
		
		await tel_send(message)
		return True
		
	except Exception as e:
		await tel_send(f"❌ srch 명령어 실행 중 오류: {e}")
		return False

