import asyncio
import sys
import os
from datetime import datetime

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from telegram.commands.token_manager import TokenManager
import utils.config as config
from utils.rate_limiter import requests

async def rank_command(token_manager, rank_type):
	"""
	rank 명령어를 처리합니다 - 실시간 시장 순위 조회
	
	Args:
		token_manager: TokenManager 인스턴스
		rank_type: 순위 타입 (1: 거래대금, 2: 상승률, 3: 거래량, 4: 인기종목)
	
	Returns:
		bool: 성공 여부
	"""
	try:
		# 토큰 확인 및 발급
		if not token_manager.token:
			token = await token_manager.get_token()
			if not token:
				await tel_send("❌ 토큰 발급에 실패했습니다.")
				return False
		else:
			token = token_manager.token
		
		# 오늘 날짜 (YYYYMMDD 형식)
		today = datetime.now().strftime('%Y%m%d')
		
		# 순위 타입에 따라 API 호출
		if rank_type == 1:
			# 거래대금 상위 (ka10032)
			await _get_trading_amount_rank(token, today)
		elif rank_type == 2:
			# 등락률 상위 (ka10027)
			await _get_fluctuation_rank(token, today)
		elif rank_type == 3:
			# 거래량 상위 (ka10030)
			await _get_volume_rank(token, today)
		elif rank_type == 4:
			# 인기종목 (실시간조회순위) (ka00198)
			await _get_market_cap_rank(token, today)
		else:
			await tel_send("❌ 잘못된 순위 타입입니다. 1~4 사이의 숫자를 입력해주세요.")
			return False
		
		return True
		
	except Exception as e:
		await tel_send(f"❌ rank 명령어 실행 중 오류: {e}")
		return False

async def _get_trading_amount_rank(token, date):
	"""거래대금 상위 조회"""
	try:
		# 키움 API 호출 (REST API)
		endpoint = '/api/dostk/rkinfo'
		url = config.get_host_url() + endpoint
		
		headers = {
			'Content-Type': 'application/json;charset=UTF-8',
			'Authorization': f'Bearer {token}',
			'appkey': config.get_app_key(),
			'appsecret': config.get_app_secret(),
			'api-id': 'ka10032',  # 거래대금 상위 TR ID
		}
		
		params = {
			'mrkt_tp': '000',       # 000:전체, 001:코스피, 101:코스닥
			'mang_stk_incls': '0',  # 관리종목포함여부 (0:미포함, 1:포함)
			'stex_tp': '3'          # 시장구분 (1:KRX, 2:NXT, 3:통합)
		}
		
		response = await requests.post(url, headers=headers, json=params)
		response_data = response.json()
		
		if response.status_code != 200:
			await tel_send(f"❌ API 호출 실패: {response.status_code}")
			return
		
		# 응답 데이터 파싱 (trde_prica_upper 키 사용)
		top_stocks = response_data.get('trde_prica_upper', [])
		
		if not top_stocks:
			await tel_send("❌ 순위 데이터를 찾을 수 없습니다.")
			return
		
		# 상위 20개만 추출
		top_stocks = top_stocks[:20] if isinstance(top_stocks, list) else []
		
		# 메시지 포맷팅
		message = "💰 [거래대금 상위 TOP 20]\n"
		message += f"{datetime.now().strftime('%Y-%m-%d')} 기준\n\n"
		
		for idx, stock in enumerate(top_stocks, 1):
			stock_name = stock.get('stk_nm') or stock.get('hts_kor_iscd', 'N/A')
			current_price = _parse_price(stock.get('cur_prc') or stock.get('stck_prpr', '0'))
			change_rate = _parse_rate(stock.get('flu_rt') or stock.get('prdy_ctrt', '0'))
			trading_amount = _parse_amount(stock.get('trde_prica', '0'))
			
			# 등락률에 따른 이모지
			emoji = "🔺" if change_rate > 0 else "🔹" if change_rate < 0 else "➡️"
			sign = "+" if change_rate > 0 else ""
			
			message += f"{idx}. {stock_name}\n"
			message += f"   {int(current_price):,}원 ({sign}{change_rate:.2f}%) {emoji} / {trading_amount}\n\n"
		
		await tel_send(message)
		
	except Exception as e:
		await tel_send(f"❌ 거래대금 순위 조회 중 오류: {e}")

async def _get_fluctuation_rank(token, date):
	"""등락률 상위 조회"""
	try:
		endpoint = '/api/dostk/rkinfo'
		url = config.get_host_url() + endpoint
		
		headers = {
			'Content-Type': 'application/json;charset=UTF-8',
			'Authorization': f'Bearer {token}',
			'appkey': config.get_app_key(),
			'appsecret': config.get_app_secret(),
			'api-id': 'ka10027',  # 등락률 상위 TR ID
		}
		
		params = {
			'mrkt_tp': '000',       # 전체
			'sort_tp': '1',         # 1:상승률순, 2:상승폭순, 3:하락률순
			'trde_qty_cnd': '0000', # 거래량조건 (0000:전체)
			'stk_cnd': '0',         # 종목조건 (0:전체)
			'crd_cnd': '0',         # 신용조건 (0:전체)
			'updown_incls': '1',    # 상하한포함 (1:포함)
			'pric_cnd': '0',        # 가격조건 (0:전체)
			'trde_prica_cnd': '0',  # 거래대금조건 (0:전체)
			'stex_tp': '3'          # 통합
		}
		
		response = await requests.post(url, headers=headers, json=params)
		response_data = response.json()
		
		if response.status_code != 200:
			await tel_send(f"❌ API 호출 실패: {response.status_code}")
			return
		
		# 응답 데이터 파싱 (pred_pre_flu_rt_upper 키 사용)
		top_stocks = response_data.get('pred_pre_flu_rt_upper', [])
		
		if not top_stocks:
			await tel_send("❌ 순위 데이터를 찾을 수 없습니다.")
			return
		
		top_stocks = top_stocks[:20] if isinstance(top_stocks, list) else []
		
		message = "📈 [상승률 상위 TOP 20]\n"
		message += f"{datetime.now().strftime('%Y-%m-%d')} 기준\n\n"
		
		for idx, stock in enumerate(top_stocks, 1):
			stock_name = stock.get('stk_nm') or stock.get('hts_kor_iscd', 'N/A')
			stock_code = stock.get('stk_cd') or stock.get('stck_shrn_iscd', '')
			current_price = _parse_price(stock.get('cur_prc') or stock.get('stck_prpr', '0'))
			change_rate = _parse_rate(stock.get('flu_rt') or stock.get('prdy_ctrt', '0'))
			
			emoji = "🔺" if change_rate > 0 else "🔹" if change_rate < 0 else "➡️"
			sign = "+" if change_rate > 0 else ""
			
			message += f"{idx}. {stock_name}"
			if stock_code:
				message += f" ({stock_code})"
			message += f"\n   {int(current_price):,}원 ({sign}{change_rate:.2f}%) {emoji}\n\n"
		
		await tel_send(message)
		
	except Exception as e:
		await tel_send(f"❌ 등락률 순위 조회 중 오류: {e}")

async def _get_volume_rank(token, date):
	"""거래량 상위 조회 (ka10030 API)"""
	try:
		# 키움 REST API - 당일거래량상위요청
		endpoint = '/api/dostk/rkinfo'
		url = config.get_host_url() + endpoint
		
		headers = {
			'Content-Type': 'application/json;charset=UTF-8',
			'Authorization': f'Bearer {token}',
			'appkey': config.get_app_key(),
			'appsecret': config.get_app_secret(),
			'api-id': 'ka10030',  # 당일거래량상위요청 TR ID
		}
		
		# API 명세서에 따른 요청 파라미터 (모든 필드는 필수)
		params = {
			'mrkt_tp': '000',            # 시장 구분: "000" 전체 (001:코스피, 101:코스닥)
			'sort_tp': '1',               # 정렬 구분: "1" 거래량순 (필수)
			'mang_stk_incls': '1',        # 관리종목 포함 여부: "1" 미포함 (0:포함, 1:미포함 권장)
			'crd_tp': '0',                # 신용 구분: "0" 전체조회
			'trde_qty_tp': '0',           # 거래량 구분: "0" 전체조회 (5:5천주↑, 10:1만주↑ 등)
			'pric_tp': '0',               # 가격 구분: "0" 전체조회 (1:1천원미만 등)
			'trde_prica_tp': '0',         # 거래대금 구분: "0" 전체조회
			'mrkt_open_tp': '0',          # 장운영 구분: "0" 전체조회 (1:장중)
			'stex_tp': '3'                # 거래소 구분: "3" 통합
		}
		
		response = await requests.post(url, headers=headers, json=params)
		response_data = response.json()
		
		# HTTP 상태 코드 확인
		if response.status_code != 200:
			await tel_send(f"❌ API 호출 실패: HTTP {response.status_code}")
			return
		
		# API 응답 코드 확인 (return_code가 0이 아니면 실패)
		return_code = response_data.get('return_code', '0')
		if return_code != '0' and return_code != 0:
			return_msg = response_data.get('return_msg', '알 수 없는 오류')
			await tel_send(f"❌ API 오류 (코드: {return_code}): {return_msg}")
			return
		
		# 응답 데이터 파싱 - tdy_trde_qty_upper 키 사용 (당일거래량상위)
		top_stocks = response_data.get('tdy_trde_qty_upper', [])
		if not top_stocks:
			# 호환성을 위해 다른 가능한 키도 시도
			top_stocks = response_data.get('trde_qty_upper', [])
		
		if not top_stocks:
			await tel_send("❌ 순위 데이터를 찾을 수 없습니다. 응답 키를 확인해주세요.")
			return
		
		# 상위 20개만 추출
		top_stocks = top_stocks[:20] if isinstance(top_stocks, list) else []
		
		# 메시지 포맷팅
		message = "📊 [거래량 상위 TOP 20]\n"
		message += f"{datetime.now().strftime('%Y-%m-%d')} 기준\n\n"
		
		for idx, stock in enumerate(top_stocks, 1):
			# 종목 정보 추출
			stock_code = stock.get('stk_cd', '')
			stock_name = stock.get('stk_nm') or stock.get('hts_kor_iscd', 'N/A')
			current_price = _parse_price(stock.get('cur_prc') or stock.get('stck_prpr', '0'))
			change_rate = _parse_rate(stock.get('flu_rt') or stock.get('prdy_ctrt', '0'))
			volume = _parse_volume(stock.get('trde_qty', '0'))
			
			# 거래대금 파싱 (ka10030 API는 trde_amt 필드를 사용하며, 백만 원 단위)
			trde_amt_million = int(stock.get('trde_amt', 0) or 0)
			trde_amt_won = trde_amt_million * 1000000  # 백만 원 단위를 원 단위로 변환
			trading_amount = _parse_amount(trde_amt_won)
			
			# 등락률에 따른 이모지
			emoji = "🔺" if change_rate > 0 else "🔹" if change_rate < 0 else "➡️"
			sign = "+" if change_rate > 0 else ""
			
			# 종목명과 종목코드 표시
			message += f"{idx}. {stock_name}"
			if stock_code:
				message += f" ({stock_code})"
			message += f"\n   {int(current_price):,}원 ({sign}{change_rate:.2f}%) {emoji}\n"
			message += f"   거래량: {volume} / 거래대금: {trading_amount}\n\n"
		
		await tel_send(message)
		
	except Exception as e:
		await tel_send(f"❌ 거래량 순위 조회 중 오류: {e}")

async def _get_market_cap_rank(token, date):
	"""실시간 종목 조회 순위 (인기종목)"""
	try:
		endpoint = '/api/dostk/stkinfo'
		url = config.get_host_url() + endpoint
		
		headers = {
			'Content-Type': 'application/json;charset=UTF-8',
			'Authorization': f'Bearer {token}',
			'appkey': config.get_app_key(),
			'appsecret': config.get_app_secret(),
			'api-id': 'ka00198',  # 실시간종목조회순위 TR ID
		}
		
		params = {
			'qry_tp': '1'  # 1:1분, 2:10분, 3:1시간, 4:당일누적
		}
		
		response = await requests.post(url, headers=headers, json=params)
		response_data = response.json()
		
		if response.status_code != 200:
			await tel_send(f"❌ API 호출 실패: {response.status_code}")
			return
		
		# 응답 데이터 파싱 (item_ing_rank 키 사용, 문서 기준)
		top_stocks = response_data.get('item_ing_rank', [])
		if not top_stocks:
			# 혹시 문서 오타일 수 있으니 item_inq_rank로도 시도
			top_stocks = response_data.get('item_inq_rank', [])
		
		if not top_stocks:
			await tel_send("❌ 순위 데이터를 찾을 수 없습니다.")
			return
		
		top_stocks = top_stocks[:20] if isinstance(top_stocks, list) else []
		
		message = "🔥 [인기종목 TOP 20]\n"
		message += f"{datetime.now().strftime('%Y-%m-%d')} 기준\n\n"
		
		for idx, stock in enumerate(top_stocks, 1):
			stock_name = stock.get('stk_nm') or stock.get('hts_kor_iscd', 'N/A')
			# 현재가 필드: past_curr_prc (문서 기준), 없으면 cur_prc로 대체
			current_price = _parse_price(stock.get('past_curr_prc') or stock.get('cur_prc', '0'))
			# 등락률 필드: base_comp_chgr (문서 기준), 없으면 flu_rt로 대체
			change_rate = _parse_rate(stock.get('base_comp_chgr') or stock.get('flu_rt', '0'))
			rank = stock.get('rank', idx)
			
			emoji = "🔺" if change_rate > 0 else "🔹" if change_rate < 0 else "➡️"
			sign = "+" if change_rate > 0 else ""
			
			message += f"{rank}. {stock_name}\n"
			message += f"   {int(current_price):,}원 ({sign}{change_rate:.2f}%) {emoji}\n\n"
		
		await tel_send(message)
		
	except Exception as e:
		await tel_send(f"❌ 인기종목 순위 조회 중 오류: {e}")

def _parse_price(price_str):
	"""가격 문자열을 숫자로 변환"""
	try:
		if isinstance(price_str, (int, float)):
			return float(price_str)
		price_str = str(price_str).strip()
		if price_str.startswith('-'):
			price_str = price_str[1:]
		return float(price_str) if price_str else 0.0
	except (ValueError, TypeError):
		return 0.0

def _parse_rate(rate_str):
	"""등락률 문자열을 숫자로 변환"""
	try:
		if isinstance(rate_str, (int, float)):
			return float(rate_str)
		rate_str = str(rate_str).strip()
		return float(rate_str) if rate_str else 0.0
	except (ValueError, TypeError):
		return 0.0

def _parse_amount(amount_str):
	"""거래대금을 읽기 쉬운 형식으로 변환 (억, 만 단위)"""
	try:
		if isinstance(amount_str, (int, float)):
			amount = float(amount_str)
		else:
			amount_str = str(amount_str).strip()
			amount = float(amount_str) if amount_str else 0.0
		
		if amount >= 100000000:  # 1억 이상
			return f"{amount / 100000000:.1f}억원"
		elif amount >= 10000:  # 1만 이상
			return f"{amount / 10000:.1f}만원"
		else:
			return f"{int(amount):,}원"
	except (ValueError, TypeError):
		return "0원"

def _parse_volume(volume_str):
	"""거래량을 읽기 쉬운 형식으로 변환"""
	try:
		if isinstance(volume_str, (int, float)):
			volume = float(volume_str)
		else:
			volume_str = str(volume_str).strip()
			volume = float(volume_str) if volume_str else 0.0
		
		if volume >= 1000000:  # 100만 이상
			return f"{volume / 1000000:.1f}백만주"
		elif volume >= 10000:  # 1만 이상
			return f"{volume / 10000:.1f}만주"
		else:
			return f"{int(volume):,}주"
	except (ValueError, TypeError):
		return "0주"

def _parse_market_cap(market_cap_str):
	"""시가총액을 읽기 쉬운 형식으로 변환"""
	try:
		if isinstance(market_cap_str, (int, float)):
			market_cap = float(market_cap_str)
		else:
			market_cap_str = str(market_cap_str).strip()
			market_cap = float(market_cap_str) if market_cap_str else 0.0
		
		if market_cap >= 1000000000000:  # 1조 이상
			return f"{market_cap / 1000000000000:.1f}조원"
		elif market_cap >= 100000000:  # 1억 이상
			return f"{market_cap / 100000000:.1f}억원"
		else:
			return f"{int(market_cap):,}원"
	except (ValueError, TypeError):
		return ""

