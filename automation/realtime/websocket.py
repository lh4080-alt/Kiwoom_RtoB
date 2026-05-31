import asyncio 
import websockets
import json
import sys
import os
import datetime
import time
from typing import Optional, Dict, List, Callable

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.config as config
from utils.collection_pool import add_to_pool
from utils.get_setting import get_setting, cached_setting
from api.login import fn_au10001 as get_token
from api.acc_val import fn_kt00004
from api.sell_stock import fn_kt10001 as sell_stock, manual_sell_events
from api.check_bal import fn_kt00001 as get_balance
from api.check_bid import fn_ka10004 as check_bid
from api.buy_stock import fn_kt10000 as buy_stock
from api.cancel_order import fn_sc10002 as cancel_order
from api.stock_info import fn_ka10001 as stock_info
from telegram.tel_send import tel_send
from utils.sold_stocks_manager import record_sold_stock, update_last_held_time
from utils.sold_stocks_manager import is_in_cooldown, get_cooldown_remaining
from utils.blocklist_checker import is_blocked, is_in_grid_trading, is_in_wave_trading
from utils.market_hour import MarketHour
from utils.stock_code_normalizer import normalize_stock_code

import logging
logger = logging.getLogger(__name__)


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

def _get_trailing_status_path():
	"""trailing_status.json 파일 경로 반환 (config/data 폴더 내부)"""
	script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	# data 폴더가 없으면 생성
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'trailing_status.json')

def _get_fcond_cooldown_path():
	"""fcond_cooldown.json 파일 경로 반환 (config/data 폴더 내부)"""
	script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	base_dir = os.path.dirname(script_dir)
	config_dir = os.path.join(base_dir, 'config')
	data_dir = os.path.join(config_dir, 'data')
	
	# data 폴더가 없으면 생성
	if not os.path.exists(data_dir):
		os.makedirs(data_dir, exist_ok=True)
	
	return os.path.join(data_dir, 'fcond_cooldown.json')

def _load_fcond_cooldown():
	"""fcond_cooldown.json 파일에서 cooldown 기록을 읽어옵니다."""
	try:
		file_path = _get_fcond_cooldown_path()
		if not os.path.exists(file_path):
			return {}
		
		with open(file_path, 'r', encoding='utf-8') as f:
			return json.load(f)
	except Exception as e:
		print(f"fcond cooldown 기록 읽기 실패: {e}")
		return {}

def _save_fcond_cooldown(data):
	"""fcond_cooldown.json 파일에 cooldown 기록을 저장합니다."""
	try:
		file_path = _get_fcond_cooldown_path()
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, ensure_ascii=False, indent=2)
		return True
	except Exception as e:
		print(f"fcond cooldown 기록 저장 실패: {e}")
		return False

def _is_fcond_in_cooldown(command_text, cooldown_hours):
	"""
	fcond 명령어가 cooldown 상태인지 확인합니다.
	
	Args:
		command_text: 실행할 명령어 텍스트 (예: "sell 123456")
		cooldown_hours: cooldown 시간 (시간 단위)
	
	Returns:
		tuple: (is_in_cooldown: bool, remaining_hours: float)
	"""
	if cooldown_hours <= 0:
		return False, 0.0
	
	cooldown_data = _load_fcond_cooldown()
	if not cooldown_data:
		return False, 0.0
	
	# 현재 시간
	now = datetime.datetime.now()
	
	# 만료된 기록 제거
	expired_keys = []
	for cmd, timestamp_str in cooldown_data.items():
		try:
			timestamp = datetime.datetime.fromisoformat(timestamp_str)
			elapsed_hours = (now - timestamp).total_seconds() / 3600.0
			if elapsed_hours >= cooldown_hours:
				expired_keys.append(cmd)
		except Exception as e:
			print(f"fcond cooldown 기록 파싱 오류: {e}")
			expired_keys.append(cmd)
	
	# 만료된 기록 삭제
	if expired_keys:
		for key in expired_keys:
			cooldown_data.pop(key, None)
		_save_fcond_cooldown(cooldown_data)
	
	# 해당 명령어가 cooldown에 있는지 확인
	if command_text in cooldown_data:
		try:
			timestamp = datetime.datetime.fromisoformat(cooldown_data[command_text])
			elapsed_hours = (now - timestamp).total_seconds() / 3600.0
			remaining_hours = cooldown_hours - elapsed_hours
			if remaining_hours > 0:
				return True, remaining_hours
		except Exception as e:
			print(f"fcond cooldown 시간 계산 오류: {e}")
	
	return False, 0.0

def _record_fcond_command(command_text):
	"""
	fcond 명령어 실행을 기록합니다.
	
	Args:
		command_text: 실행된 명령어 텍스트 (예: "sell 123456")
	"""
	try:
		cooldown_data = _load_fcond_cooldown()
		now = datetime.datetime.now()
		cooldown_data[command_text] = now.isoformat()
		_save_fcond_cooldown(cooldown_data)
	except Exception as e:
		print(f"fcond 명령어 기록 저장 실패: {e}")

def _load_trailing_status():
	"""trailing_status.json 파일에서 트레일링 스탑 고점 정보를 읽어옵니다."""
	try:
		file_path = _get_trailing_status_path()
		if not os.path.exists(file_path):
			return {}
		
		with open(file_path, 'r', encoding='utf-8') as f:
			return json.load(f)
	except Exception as e:
		print(f"트레일링 스탑 상태 읽기 실패: {e}")
		return {}

def _save_trailing_status(data):
	"""trailing_status.json 파일에 트레일링 스탑 고점 정보를 저장합니다."""
	try:
		file_path = _get_trailing_status_path()
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, ensure_ascii=False, indent=2)
		return True
	except Exception as e:
		print(f"트레일링 스탑 상태 저장 실패: {e}")
		return False

class UnifiedWebSocket:
	"""통합 웹소켓 클래스 - 모든 웹소켓 작업을 하나의 연결로 처리"""
	
	def __init__(self, token_manager, on_connection_closed=None, on_relogin_complete=None, process_command_callback=None):
		self.socket_url = None  # 연결 시점에 동적으로 설정
		self.token_manager = token_manager
		self.websocket = None
		self.connected = False
		self.keep_running = True
		self.receive_task = None
		self.sync_task = None
		self.on_connection_closed = on_connection_closed
		self.on_relogin_complete = on_relogin_complete
		self.process_command_callback = process_command_callback  # fcond 명령어 실행을 위한 콜백
		self.token = None
		
		# 등록된 조건식 추적
		self.registered_seqs = set()
		
		# 조건식 목록 조회를 위한 Future 딕셔너리
		self.pending_requests: Dict[str, asyncio.Future] = {}

		# CNSRLST(조건식 목록 조회) 선행 호출 보장용 상태/락
		self._condition_list_loaded = False
		self._condition_list_lock = asyncio.Lock()
		
		# 기능 1 활성화 여부
		self.feature_1_active = False
		
		# 기능 2, 5, 6 활성화 여부
		self.feature_2_active = False  # 익절/손절
		self.feature_5_active = False  # 트레일링 스탑
		self.feature_6_active = False  # 돌파 매수
		
		# Portfolio Manager: 보유 종목 상태 관리
		self.portfolio = {}  # {종목코드: {name, avg_price, quantity, high_price, status}}
		self.break_stock_list = set()  # 기능 6 감시 대상
		
		# 오전 8시 30분 재로그인을 위한 변수
		self.last_relogin_date = None
		self.relogin_task = None
		self.is_relogging = False  # 재로그인 중인지 표시하는 플래그
		
		# 로그 출력 빈도 제한을 위한 종목별 마지막 출력 시간 추적
		self.last_log_time = {}  # {종목코드: 마지막_출력_시간}
		
		# 주문체결(00) 로그 중복 출력 방지용 (연속 동일 종목/상태/구분은 출력 생략)
		self._last_printed_order_exec_key = None  # (stock_code, order_type, order_status)
		
		# 특정 종목에 대한 매수/매도 트랜잭션 진행 중 표시(인메모리 락)
		# - 동일 종목 중복 매수 방지
		# - 매도 주문 대기 중 쿨다운 무시 재매수 방지
		self.processing_stocks = set()
		
		# 이전 보유 종목 목록 (매수/매도 완료 알림용)
		self.previous_held_stocks = set()  # {종목코드}
		
		# === REG 무한 루프 방지: 실시간 등록된 종목 상태 관리 ===
		# 등록된 종목 추적: {종목코드: {type: ['0B'], last_registered: timestamp}}
		self.registered_items = {}  # {종목코드: {'type': ['0B'], 'last_registered': float}}
		
		# REG 요청 디바운싱을 위한 대기 중인 요청 큐
		self._pending_reg_requests = []  # [(stock_codes, type, timestamp), ...]
		self._reg_debounce_task = None  # 디바운싱 태스크
		self._reg_debounce_lock = asyncio.Lock()  # 디바운싱 락
		self._reg_debounce_delay = 0.8  # 디바운싱 딜레이 (초)
		
		# 에러 핸들링: 1700 에러 발생 시 백오프
		self._reg_backoff_until = 0.0  # REG 요청 중단 시간 (타임스탬프)
		self._reg_backoff_duration = 5.0  # 백오프 지속 시간 (초)
		self._reg_error_count = 0  # 연속 에러 카운트
		
	async def connect(self, token):
		"""WebSocket 서버에 연결합니다."""
		try:
			# 연결 시점에 현재 설정(실전/모의)에 맞는 URL 가져오기
			base_socket_url = config.get_socket_url()
			self.socket_url = base_socket_url + '/api/dostk/websocket'
			
			self.token = token
			self.websocket = await websockets.connect(self.socket_url)
			self.connected = True
			# 새 연결에서는 CNSRLST를 다시 선행 호출해야 함
			self._condition_list_loaded = False
			print("서버와 연결을 시도 중입니다.")

			# 로그인 패킷
			param = {
				'trnm': 'LOGIN',
				'token': token
			}

			print('실시간 시세 서버로 로그인 패킷을 전송합니다.')
			await self.send_message(message=param)

		except Exception as e:
			print(f'Connection error: {e}')
			self.connected = False
			self.websocket = None

	async def send_message(self, message, token=None):
		"""서버에 메시지를 보냅니다."""
		# 재로그인 중이면 연결이 끊어져 있어도 재연결하지 않음 (재로그인 메시지는 기존 연결로 전송)
		if not self.connected and not self.is_relogging:
			if token:
				await self.connect(token)
		if self.connected and self.websocket:
			# message가 문자열이 아니면 JSON으로 직렬화
			if not isinstance(message, str):
				message = json.dumps(message)

			await self.websocket.send(message)
			print(f'Message sent: {message}')

	async def receive_messages(self):
		"""서버에서 오는 메시지를 수신하여 처리합니다."""
		while self.keep_running and self.connected and self.websocket:
			raw_message = None
			try:
				# 서버로부터 수신한 메시지를 받음
				raw_message = await self.websocket.recv()
				# JSON 형식으로 파싱
				response = json.loads(raw_message)
				trnm = response.get('trnm')

				# LOGIN 응답 처리
				if trnm == 'LOGIN':
					if response.get('return_code') != 0:
						print('로그인 실패하였습니다. : ', response.get('return_msg'))
						# 재로그인 중이 아니면 연결 종료
						if not self.is_relogging:
							await self.disconnect()
					else:
						print('로그인 성공하였습니다.')
						# 재로그인 중이면 기능 등록은 _relogin_scheduler에서 처리하므로 여기서는 스킵
						if not self.is_relogging:
							# 기능 1이 활성화되어 있으면 조건식 등록
							if self.feature_1_active:
								await self._register_conditions()
							# 기능 2 또는 5가 활성화되어 있으면 portfolio 초기화 및 0B 등록
							if self.feature_2_active or self.feature_5_active:
								await self._initialize_portfolio()
								await self._register_stock_quotes()
							# 기능 6이 활성화되어 있으면 감시 목록 등록
							if self.feature_6_active:
								await self._register_breakout_stocks()
							
							# === [추가된 부분] 주문체결(00) 등록 호출 ===
							# 주문체결 알림은 어떤 기능이 켜져 있든 기본적으로 받아야 하므로 항상 등록
							# 다른 시세 등록(refresh=0 사용 가능성 있음) 후에 실행하여 덮어쓰기 방지
							await asyncio.sleep(0.5)  # 안전을 위해 약간 대기
							await self._register_order_execution()

				# PING 응답 처리
				elif trnm == 'PING':
					print(f'PING 메시지 수신: {response}')
					await self.send_message(response)

				# CNSRLST (조건식 목록) 응답 처리
				elif trnm == 'CNSRLST':
					if 'CNSRLST' in self.pending_requests:
						future = self.pending_requests.pop('CNSRLST')
						if not future.done():
							future.set_result(response.get('data'))
					else:
						print(f'조건식 목록 응답 수신: {response}')

				# CNSRREQ (조건식 등록) 응답 처리
				elif trnm == 'CNSRREQ':
					seq = response.get('seq', '알 수 없음')
					return_code = response.get('return_code', -1)
					return_msg = response.get('return_msg', '')

					# CNSRREQ 응답 raw 캡처 (cond0 스냅샷 누락 등 재발 시 진단용).
					# 평시 INFO 레벨이라 안 보임. settings.json log_level=DEBUG 시 출력.
					if logger.isEnabledFor(logging.DEBUG):
						data_len = len(response.get('data', []) or [])
						top_keys = list(response.keys())
						logger.debug("CNSRREQ raw: seq=%s rc=%s top_keys=%s data_len=%s",
						             seq, return_code, top_keys, data_len)
						logger.debug("CNSRREQ raw data: %s", response.get('data'))

					if return_code == 0:
						# 조건식 등록 성공 — 초기 스냅샷(현재 조건 만족 종목) 포함될 수 있음
						# PDF ka10173 스펙: data 항목 = {'jmcode': 'A005930'}
						# 구버전 호환: {'values': {'9001': '005930'}}
						data = response.get('data', [])
						stock_codes = []
						if data:
							for item in data:
								code = None
								if isinstance(item, dict):
									code = item.get('jmcode')
									if not code and 'values' in item:
										code = item['values'].get('9001')
								elif isinstance(item, str):
									code = item
								if not code:
									continue
								code = str(code).lstrip('A').strip()
								if not code:
									continue
								stock_codes.append(code)

						if stock_codes:
							codes_str = ", ".join(stock_codes)
							print(f'✅ 조건식 등록 성공 (seq: {seq}) - 초기 스냅샷 {len(stock_codes)}종목: {codes_str}')
							await tel_send(f'📊 조건식 등록 (seq={seq}) - {len(stock_codes)}종목\n{codes_str}')
							seq_str = str(seq) if seq != '알 수 없음' else None
							for code in stock_codes:
								if code in self.processing_stocks:
									print(f"⏳ {code}: 이미 처리 중이므로 스냅샷 신호를 무시합니다.")
									continue
								self.processing_stocks.add(code)
								asyncio.create_task(self._safe_add_to_pool(code, seq_id=seq_str))
						else:
							print(f'✅ 조건식 등록 성공 (seq: {seq}) - 초기 스냅샷 없음')
							await tel_send(f'📊 조건식 등록 (seq={seq}) - 초기 스냅샷 없음')
					else:
						# 조건식 등록 실패
						print(f'❌ 조건식 등록 실패 (seq: {seq}) - {return_msg}')

				# CNSRCLR (조건식 해제) 응답 처리
				elif trnm == 'CNSRCLR':
					seq = response.get('seq', '알 수 없음')
					return_code = response.get('return_code', -1)
					return_msg = response.get('return_msg', '')
					
					if return_code == 0:
						print(f'✅ 조건식 해제 성공 (seq: {seq})')
					else:
						print(f'❌ 조건식 해제 실패 (seq: {seq}) - {return_msg}')

				# REAL (실시간 데이터) 응답 처리
				elif trnm == 'REAL':
					if response.get('data'):
						items = response['data']
						if items:
							# 조건식 정보 추출
							seq = response.get('seq')
							
							# items 리스트를 순회하며 타입별로 분기 처리
							for item in items:
								if not isinstance(item, dict):
									continue
								
								# 타입 확인
								item_type = item.get('type')
								
								# 0B: 주식체결 (현재가)
								if item_type == '0B':
									await self._handle_stock_quote(item)
									# [수정] 응답 로그 간소화 및 파싱 출력
									code = item.get('item', 'Unknown')
									values = item.get('values') or {}
									cur_price = values.get('10', '')  # 현재가
									rate = values.get('12', '')       # 등락률
									vol = values.get('15', '')        # 거래량
									# 시세 데이터는 빈번하므로 필요시에만 출력하거나 주석 처리 가능
									# print(f"📈 [시세] {code} | 현재가: {cur_price} | 등락률: {rate}% | 거래량: {vol}")
									
								# 04: 잔고
								elif item_type == '04':
									await self._handle_balance(item)
									# [수정] 응답 로그 간소화 및 파싱 출력
									code = item.get('item', 'Unknown')
									values = item.get('values') or {}
									name = values.get('302', '')
									qty = values.get('930', '0')      # 보유수량
									profit = values.get('8019', '0')  # 수익률
									print(f"💰 [잔고변경] {name}({code}) | 보유수량: {qty} | 수익률: {profit}%")
								# 00: 주문체결
								elif item_type == '00':
									await self._handle_order_execution(item)
									# [수정] 응답 로그 간소화 및 파싱 출력
									values = item.get('values') or {}
									ord_type = values.get('905', '')  # 주문구분 (매수/매도)
									status = values.get('913', '')    # 주문상태 (접수/체결)
									name = values.get('302', '')      # 종목명
									qty = values.get('900', '')       # 주문수량
									price = values.get('901', '')     # 주문가격
									stock_code = values.get('9001') or item.get('item') or 'Unknown'
									
									# 같은 종목의 동일 상태/구분 주문체결 로그가 연속으로 찍히는 것을 방지
									cur_key = (stock_code, ord_type, status)
									if self._last_printed_order_exec_key != cur_key:
										print(f"✅ [주문체결] {name}({stock_code}) | {ord_type} | {status} | 수량: {qty} | 가격: {price}")
										self._last_printed_order_exec_key = cur_key
								# 조건검색 처리 (안전한 접근)
								else:
									# [수정] 응답 로그 간소화 및 파싱 출력
									code = item.get('item', 'Unknown')
									values = item.get('values') or {}
									msg_type = item_type
									
									# [1h] VI 발동/해제 (API 문서 p.533)
									if msg_type == '1h':
										vi_type = values.get('1225', '')  # VI적용구분
										price = values.get('1221', '')    # 발동가격
										print(f"⚠️ [VI발동] {code} | {vi_type} | 가격: {price}")
									# [0s] 장운영 구분 (API 문서 p.526)
									elif msg_type == '0s':
										status_code = values.get('215', '')
										time_str = values.get('20', '')
										print(f"⏰ [장운영] 상태코드: {status_code} | 시간: {time_str}")
									# 기타 실시간 데이터
									else:
										print(f"📡 [실시간] 타입: {msg_type} | 종목: {code}")
									
									# item['values'] 내에 '9001' 키가 존재하는 경우에만 chk_n_buy 실행
									if isinstance(item.get('values'), dict) and '9001' in item['values']:
										jmcode = item['values']['9001']
										
										# 조건식 인덱스(seq) 추출: 상위에서 가져온 seq가 None이면 values 내부의 FID 841을 확인
										current_seq = seq
										if current_seq is None:
											current_seq = item['values'].get('841')
										
										# 이벤트 종류 확인 (I: 편입, D: 이탈)
										# 작업 지시서에 따르면 011이지만, 실제 API에서는 다른 FID를 사용할 수 있음
										# 일단 편입(I)인 경우에만 처리 (이벤트 타입이 없으면 편입으로 간주)
										event_type = values.get('011', 'I')  # 기본값은 I (편입)
										
										# 편입(Insert)인 경우에만 처리
										if event_type == 'I':
											# --- 동시성 제어(종목 단위 인메모리 락) ---
											# Check-Then-Act: 이미 처리 중인 종목이면 신호 무시
											if jmcode in self.processing_stocks:
												print(f"⏳ {jmcode}: 이미 처리 중이므로 매수 신호를 무시합니다.")
												continue
											
											# 처리 시작 표시 후, 안전 래퍼로 수집풀 적재
											self.processing_stocks.add(jmcode)
											seq_str = str(current_seq) if current_seq is not None else None
											asyncio.create_task(self._safe_add_to_pool(jmcode, seq_id=seq_str))
											await asyncio.sleep(1)
											
											# 조건식 정보 출력
											if current_seq is not None:
												print(f'📊 조건식 (seq: {current_seq})에서 종목 수신: {jmcode}')
											
											# fcond 처리 로직 추가
											if current_seq is not None and self.process_command_callback:
												try:
													# fcond_rules 가져오기
													from utils.get_setting import get_setting
													fcond_rules = get_setting('fcond_rules', {})
													if not isinstance(fcond_rules, dict):
														fcond_rules = {}
													
													# 조건식 인덱스를 문자열로 변환
													seq_str = str(current_seq).strip()
													
													# fcond_rules에 해당 조건식 인덱스가 있는지 확인
													if seq_str in fcond_rules:
														# 명령어 템플릿 가져오기
														template = fcond_rules[seq_str]
														
														# 종목코드 포맷 확인 및 정규화 (A005930 -> 005930)
														stock_code = normalize_stock_code(jmcode)
														
														# ()를 종목코드로 치환
														cmd_text = template.replace('()', stock_code)
														
														# cooldown 설정 가져오기
														cooldown_hours = get_setting('fcond_cooldown_hours', 0)
														
														# cooldown 체크
														is_in_cooldown, remaining_hours = _is_fcond_in_cooldown(cmd_text, cooldown_hours)
														
														if is_in_cooldown:
															# cooldown 중이면 실행하지 않음
															print(f"[FCOND] Cooldown! Command: {cmd_text}, Remaining: {remaining_hours:.2f} hours")
															await tel_send(f"⏸️ fcond 명령어가 cooldown 중입니다.\n명령어: {cmd_text}\n남은 시간: {remaining_hours:.2f}시간")
														else:
															# cooldown이 아니면 실행
															# 로그 출력
															print(f"[FCOND] Triggered! Index: {seq_str}, Code: {stock_code} -> Executing: {cmd_text}")
															
															# 텔레그램 메시지 전송 (조건식 정보 포함)
															await tel_send(f"조건식: {seq_str}에서 fcond 명령어 실행")
															
															# 명령어 실행 (비동기 태스크로 실행하여 블로킹 방지)
															asyncio.create_task(self.process_command_callback(cmd_text))
															
															# 명령어 실행 기록 저장
															if cooldown_hours > 0:
																_record_fcond_command(cmd_text)
														
												except Exception as e:
													print(f"[FCOND] 오류 발생: {e}")

				# REG 응답 처리 (에러 코드 1700 등 처리)
				elif trnm == 'REG':
					return_code = response.get('return_code', 0)
					return_msg = response.get('return_msg', '')
					
					if return_code == 0:
						# 성공: 등록된 종목 정보 업데이트
						data = response.get('data', [])
						if data:
							for item in data:
								if isinstance(item, dict):
									items = item.get('item', [])
									item_type = item.get('type', [])
									if isinstance(items, str):
										items = [items]
									if isinstance(item_type, str):
										item_type = [item_type]
									
									for stock_code in items:
										if stock_code:
											self.registered_items[stock_code] = {
												'type': item_type,
												'last_registered': time.time()
											}
					elif return_code == 1700:
						# 허용된 요청 개수 초과 - 백오프 시작
						print(f'⚠️ [REG 에러 1700] 허용된 요청 개수 초과. {self._reg_backoff_duration}초 동안 REG 요청을 중단합니다.')
						self._reg_backoff_until = time.time() + self._reg_backoff_duration
						self._reg_error_count += 1
						
						# 에러 카운트가 증가하면 백오프 시간도 증가
						if self._reg_error_count >= 3:
							self._reg_backoff_duration = min(self._reg_backoff_duration * 1.5, 30.0)
							print(f'⚠️ [REG] 연속 에러 {self._reg_error_count}회. 백오프 시간을 {self._reg_backoff_duration:.1f}초로 증가합니다.')
					else:
						# 기타 에러
						print(f'❌ [REG 에러] return_code: {return_code}, return_msg: {return_msg}')
						if return_code > 0:
							self._reg_error_count += 1
				
				# [수정] REAL, PING, LOGIN 관련 메시지가 아닌 경우에만 원본 출력
				elif trnm not in ['PING', 'LOGIN', 'CNSRLST', 'CNSRREQ', 'CNSRCLR', 'REG']:
					resp_str = str(response)
					if len(resp_str) > 100:
						print(f'📩 [기타응답] {trnm}: {resp_str[:100]}... (생략됨)')
					else:
						print(f'📩 [기타응답] {trnm}: {resp_str}')

			except websockets.ConnectionClosed:
				print('Connection closed by the server')
				self.connected = False
				if self.websocket:
					try:
						await self.websocket.close()
					except:
						pass
				
				# 재로그인 중이면 연결 종료 콜백을 호출하지 않음
				if not self.is_relogging:
					# 연결 종료 콜백 호출
					if self.on_connection_closed:
						try:
							await self.on_connection_closed()
						except Exception as e:
							print(f'콜백 실행 중 오류: {e}')
				else:
					print("재로그인 중이므로 연결 종료 콜백을 호출하지 않습니다.")
				break
			
			except json.JSONDecodeError as e:
				print(f'JSON 파싱 오류: {e}')
				print(f'수신한 원본 메시지: {raw_message if raw_message else "수신 실패"}')
				continue
			
			except Exception as e:
				print(f'receive_messages에서 예외 발생: {type(e).__name__}: {e}')
				print(f'연결 상태: connected={self.connected}, websocket={self.websocket is not None}')
				
				# 연결이 끊어진 것으로 보이면 연결 상태 확인
				if self.websocket:
					try:
						await asyncio.wait_for(self.websocket.ping(), timeout=2)
						print('연결은 유지되고 있습니다. 메시지 수신 계속...')
						continue
					except Exception as ping_e:
						print(f'연결 확인 실패: {ping_e}')
						self.connected = False
						# 재로그인 중이면 연결 종료 콜백을 호출하지 않음
						if not self.is_relogging:
							if self.on_connection_closed:
								try:
									await self.on_connection_closed()
								except Exception as callback_e:
									print(f'콜백 실행 중 오류: {callback_e}')
						else:
							print("재로그인 중이므로 연결 종료 콜백을 호출하지 않습니다.")
						break
				else:
					print('websocket이 None입니다. 루프 종료')
					break

	async def _safe_add_to_pool(self, stock_code, seq_id=None):
		"""
		수집풀 적재를 안전하게 감싸는 비동기 래퍼.
		- try...finally로 예외와 무관하게 processing_stocks 락을 반드시 해제합니다.
		"""
		try:
			await add_to_pool(stock_code, seq_id=seq_id)
		except Exception as e:
			print(f"수집풀 적재 중 오류({stock_code}): {type(e).__name__}: {e}")
		finally:
			# deadlock 방지: 어떤 경우든 락 해제
			if stock_code in self.processing_stocks:
				self.processing_stocks.remove(stock_code)

	async def disconnect(self):
		"""WebSocket 연결 종료"""
		self.keep_running = False
		if self.connected and self.websocket:
			try:
				await self.websocket.close()
			except Exception as e:
				print(f'WebSocket close error: {e}')
			finally:
				self.connected = False
				self.websocket = None
				# 연결 종료 시 다음 연결에서 다시 CNSRLST 선행 호출
				self._condition_list_loaded = False
				# 연결 종료 시 등록된 종목 상태 초기화
				self.registered_items.clear()
				print('Disconnected from WebSocket server')

	async def _ensure_condition_list_loaded(self):
		"""
		키움증권 API 명세 준수:
		- 실시간 조건검색(CNSRREQ) 요청 전에 조건식 목록 조회(CNSRLST)가 선행되어야 함
		"""
		async with self._condition_list_lock:
			if self._condition_list_loaded:
				return
			
			print("📋 조건식 목록 조회(CNSRLST)를 시도합니다... (조건식 등록(CNSRREQ) 이전 필수)")
			data = await self.get_condition_list()
			if data is None:
				print("⚠️ 조건식 목록 조회(CNSRLST) 응답이 없어도, 조건식 등록(CNSRREQ)을 시도합니다.")
				return
			
			self._condition_list_loaded = True
			print("📋 조건식 목록 조회(CNSRLST) 완료. 이제 조건식 등록(CNSRREQ)을 진행합니다.")

	async def _register_conditions(self):
		"""설정에서 조건식들을 등록합니다."""
		# CNSRREQ 전에 CNSRLST를 반드시 선행 호출 (재로그인/자동재등록 포함 공통 보장)
		await self._ensure_condition_list_loaded()

		# search_seq 가져오기
		seq_value = get_setting('search_seq', '0')
		
		# search_seq가 리스트인지 문자열인지 확인
		if isinstance(seq_value, list):
			seq_list = [str(s).strip() for s in seq_value if s]
		else:
			# 문자열인 경우 공백이나 쉼표로 분리하여 리스트로 변환
			if isinstance(seq_value, str):
				seq_list = [s.strip() for s in seq_value.replace(',', ' ').split() if s.strip()]
			else:
				# 숫자인 경우 리스트로 변환
				seq_list = [str(seq_value)] if seq_value else []

		# fcond_rules 가져오기
		fcond_rules = get_setting('fcond_rules', {})
		if not isinstance(fcond_rules, dict):
			fcond_rules = {}
		
		# fcond_rules의 키(조건식 번호)를 문자열로 변환하여 리스트로 추출
		fcond_seq_list = [str(k).strip() for k in fcond_rules.keys() if k]
		
		# search_seq와 fcond_rules 병합 (중복 제거, 모두 문자열로 통일)
		all_seqs_set = set()
		for seq in seq_list:
			all_seqs_set.add(str(seq).strip())
		for seq in fcond_seq_list:
			all_seqs_set.add(str(seq).strip())
		
		# 중복 제거된 리스트로 변환
		all_seqs_list = sorted(list(all_seqs_set))

		# 실시간 항목 등록 (여러 조건식 각각 등록)
		await asyncio.sleep(1)
		for seq in all_seqs_list:
			await self.send_message({ 
				'trnm': 'CNSRREQ', # 서비스명
				'seq': seq, # 조건검색식 일련번호
				'search_type': '1', # 조회타입
				'stex_tp': 'K', # 거래소구분
			}, self.token)
			self.registered_seqs.add(seq)
			print(f'실시간 검색 항목 등록: seq {seq}')
			
			# 재로그인 중이 아닐 때만 각 조건식 등록을 텔레그램으로 알림
			if not self.is_relogging:
				await tel_send(f"📊 조건식 등록: {seq}번")
			
			# 서버 부하 방지 및 안정적인 처리 보장을 위해 0.2초 대기
			await asyncio.sleep(0.2)
		
		# 재로그인 중이 아닐 때만 "등록 완료" 메시지 출력
		if not self.is_relogging:
			print(f'실시간 검색 항목 등록 완료. 등록된 조건식: {", ".join(all_seqs_list)}')

	async def _clear_all_conditions(self):
		"""등록된 모든 조건식을 해제합니다."""
		# 재로그인 중이면 알림 없이 조용히 처리
		if self.is_relogging:
			seqs_to_clear = list(self.registered_seqs)
			for seq in seqs_to_clear:
				await self.send_message({
					'trnm': 'CNSRCLR',
					'seq': seq
				}, self.token)
			self.registered_seqs.clear()
			return
		
		# 현재 등록된 조건식들을 복사
		seqs_to_clear = list(self.registered_seqs)
		
		for seq in seqs_to_clear:
			await self.send_message({
				'trnm': 'CNSRCLR',
				'seq': seq
			}, self.token)
			print(f'조건식 해제: seq {seq}')
		
		self.registered_seqs.clear()
		print('모든 조건식이 해제되었습니다.')

	async def start(self, token):
		"""
		웹소켓을 시작합니다.
		"""
		try:
			# keep_running 플래그를 True로 리셋
			self.keep_running = True
			
			# 이미 웹소켓이 돌고 있다면 종료
			if self.receive_task and not self.receive_task.done():
				self.receive_task.cancel()
				try:
					await self.receive_task
				except asyncio.CancelledError:
					pass
				self.receive_task = None
				await self.disconnect()
			
			# 잔고 동기화 태스크가 돌고 있다면 종료
			if self.sync_task and not self.sync_task.done():
				self.sync_task.cancel()
				try:
					await self.sync_task
				except asyncio.CancelledError:
					pass
				self.sync_task = None

			# WebSocket 연결
			await self.connect(token)
			
			# 연결이 성공했는지 확인
			if not self.connected:
				print('WebSocket 연결에 실패했습니다.')
				return False

			# WebSocket 메시지 수신을 백그라운드에서 실행합니다.
			self.receive_task = asyncio.create_task(self.receive_messages())
			
			# 10초 주기 잔고 동기화(폴링) 태스크 시작
			if not self.sync_task or self.sync_task.done():
				self.sync_task = asyncio.create_task(self._sync_portfolio_task())

			# 장 시작 5분 전 재로그인 태스크 시작
			if not self.relogin_task or self.relogin_task.done():
				self.relogin_task = asyncio.create_task(self._relogin_scheduler())

			print('웹소켓이 시작되었습니다.')
			return True
			
		except Exception as e:
			print(f'웹소켓 시작 실패: {e}')
			return False

	async def stop(self):
		"""
		웹소켓 연결을 종료합니다.
		"""
		try:
			# 재로그인 태스크 중지
			if self.relogin_task and not self.relogin_task.done():
				self.relogin_task.cancel()
				try:
					await self.relogin_task
				except asyncio.CancelledError:
					pass
			
			# 잔고 동기화 태스크 중지
			if self.sync_task and not self.sync_task.done():
				self.sync_task.cancel()
				try:
					await self.sync_task
				except asyncio.CancelledError:
					pass
				self.sync_task = None
			
			# 이미 웹소켓이 돌고 있다면 종료
			if self.receive_task and not self.receive_task.done():
				self.receive_task.cancel()
				try:
					await self.receive_task
				except asyncio.CancelledError:
					pass
				self.receive_task = None
				await self.disconnect()
			
			print('웹소켓이 중지되었습니다.')
			return True
			
		except Exception as e:
			print(f'웹소켓 중지 실패: {e}')
			return False

	async def _relogin_scheduler(self):
		"""장 시작 1분 전에 재로그인하는 스케줄러"""
		try:
			while self.keep_running:
				now = datetime.datetime.now()
				today = now.date()
				
				# MarketHour에서 장 시작 시간 가져오기
				market_start_hour = MarketHour.get_start_hour()
				market_start_minute = MarketHour.get_start_minute()
				
				# 장 시작 1분 전 시간 계산
				relogin_hour = market_start_hour
				relogin_minute = market_start_minute - 1
				if relogin_minute < 0:
					relogin_hour -= 1
					relogin_minute += 60
				
				# 장 시작 1분 전 체크
				if now.hour == relogin_hour and now.minute == relogin_minute:
					# 오늘 이미 재로그인했는지 확인
					if self.last_relogin_date != today:
						# 재로그인 중 플래그를 먼저 설정 (연결 종료 콜백에서 알림/기능 중지 방지)
						# 플래그를 먼저 설정하여 연결이 끊어져도 알림이 가지 않도록 함
						self.is_relogging = True
						
						# 사전 알림 메시지 발송
						await tel_send("자동매매 시작 1분 전입니다. 토큰 갱신 및 재로그인을 진행합니다.")
						
						print(f'자동매매 시작 시간({market_start_hour}:{market_start_minute:02d}) 1분 전 재로그인을 시작합니다...')
						
						# 재로그인 전 활성 기능 저장
						active_features_before_relogin = {
							'feature_1': self.feature_1_active,
							'feature_2': self.feature_2_active,
							'feature_5': self.feature_5_active,
							'feature_6': self.feature_6_active
						}
						
						try:
							# 새 토큰 강제 발급 (기존 토큰 캐시 무시)
							new_token = await self.token_manager.get_token(force_refresh=True)
							if new_token:
								# 기능 1이 활성화되어 있으면 조건식들 저장
								was_feature_1_active = self.feature_1_active
								
								# 재로그인 전에 기존 조건식들 해제
								if was_feature_1_active and self.registered_seqs:
									await self._clear_all_conditions()
									await asyncio.sleep(0.5)
								
								# 기존 연결 종료
								print("기존 웹소켓 연결을 종료합니다...")
								if self.connected and self.websocket:
									try:
										# 동기화 태스크가 실행 중이면 종료
										if self.sync_task and not self.sync_task.done():
											self.sync_task.cancel()
											try:
												await self.sync_task
											except asyncio.CancelledError:
												pass
											self.sync_task = None
										
										# receive_task가 실행 중이면 종료
										if self.receive_task and not self.receive_task.done():
											self.receive_task.cancel()
											try:
												await self.receive_task
											except asyncio.CancelledError:
												pass
											self.receive_task = None
										
										# 웹소켓 연결 종료
										await self.disconnect()
									except Exception as e:
										print(f"기존 연결 종료 중 오류 (무시): {e}")
								
								# 잠시 대기
								await asyncio.sleep(1)
								
								# keep_running 플래그를 True로 리셋 (재연결을 위해)
								self.keep_running = True
								
								# 새 토큰으로 연결
								print("새 토큰으로 웹소켓을 재연결합니다...")
								# 재연결 전 등록된 종목 상태 초기화 (새 연결이므로)
								self.registered_items.clear()
								await self.connect(new_token)
								
								# 연결 확인
								if not self.connected:
									raise Exception("웹소켓 재연결 실패")
								
								# WebSocket 메시지 수신을 백그라운드에서 재시작
								if not self.receive_task or self.receive_task.done():
									self.receive_task = asyncio.create_task(self.receive_messages())
								
								# 10초 주기 잔고 동기화(폴링) 태스크 재시작
								if not self.sync_task or self.sync_task.done():
									self.sync_task = asyncio.create_task(self._sync_portfolio_task())
								
								# 로그인 완료 대기 (서버 응답 대기)
								await asyncio.sleep(2)
								
								# 토큰 동기화 및 확인
								self.token = new_token
								self.last_relogin_date = today
								
								# 토큰 동기화 상태 확인
								manager_token = self.token_manager.token
								websocket_token = self.token
								tokens_synced = (manager_token == websocket_token == new_token)
								
								if tokens_synced:
									print(f"✅ 토큰 동기화 완료 - 모든 참조가 동일: {new_token[:10]}...")
								else:
									print(f"⚠️ 토큰 동기화 불일치 감지:")
									print(f"  - TokenManager: {manager_token[:10] if manager_token else 'None'}...")
									print(f"  - WebSocket: {websocket_token[:10] if websocket_token else 'None'}...")
									print(f"  - New Token: {new_token[:10] if new_token else 'None'}...")
									# 강제 동기화
									self.token_manager.token = new_token
									self.token = new_token
									print("🔧 강제 토큰 동기화 완료")
								
								# 기능 1이 활성화되어 있었으면 조건식 재등록
								if was_feature_1_active:
									await asyncio.sleep(1)
									await self._register_conditions()
								
								# 기능 2 또는 5가 활성화되어 있으면 portfolio 재초기화 및 0B 재등록
								if self.feature_2_active or self.feature_5_active:
									await asyncio.sleep(1)
									await self._initialize_portfolio()
									# 재연결 시에는 refresh='0'으로 초기화하여 재등록
									await self._register_stock_quotes(force_refresh=True)
								
								# 기능 6: 돌파 매수 감시 재등록
								if self.feature_6_active:
									await asyncio.sleep(1)
									await self._register_breakout_stocks()
								
								# === [추가된 부분] 주문체결(00) 재등록 호출 ===
								# 재로그인 후에도 주문체결 알림은 항상 등록해야 함
								await asyncio.sleep(0.5)
								await self._register_order_execution()
								
								print(f'장 시작 시간({market_start_hour}:{market_start_minute:02d}) 1분 전 재로그인 완료')
								
								# 재로그인 완료 콜백 호출
								if self.on_relogin_complete:
									try:
										await self.on_relogin_complete(new_token, active_features_before_relogin)
									except Exception as e:
										print(f'재로그인 완료 콜백 실행 중 오류: {e}')
								
								# 재로그인이 성공적으로 완료된 후에만 플래그 해제
								# 재로그인 완료 후 약간의 지연을 두어 연결 종료 콜백이 먼저 처리되도록 함
								await asyncio.sleep(2)
							else:
								print('재로그인 실패: 토큰 발급 실패')
						except Exception as e:
							print(f'재로그인 중 오류 발생: {e}')
							raise
						finally:
							# try-finally 블록으로 플래그를 확실히 해제
							# 에러가 발생하더라도 플래그가 해제되어 이후 자동 재연결이 정상 작동하도록 함
							self.is_relogging = False
							print("재로그인 플래그 해제 완료")
						
						# 1분 대기하여 중복 실행 방지
						await asyncio.sleep(60)
				
				await asyncio.sleep(30)  # 30초마다 체크
				
		except asyncio.CancelledError:
			print("재로그인 스케줄러가 중지되었습니다")
		except Exception as e:
			print(f'재로그인 스케줄러 오류: {e}')

	async def get_condition_list(self, timeout=10.0):
		"""
		조건식 목록을 조회합니다.
		
		Returns:
			list: 조건식 목록 데이터
		"""
		try:
			# Future 생성
			future = asyncio.Future()
			self.pending_requests['CNSRLST'] = future
			
			# 조건식 목록 조회 요청
			await self.send_message({
				'trnm': 'CNSRLST'
			}, self.token)
			
			# 응답 대기
			data = await asyncio.wait_for(future, timeout=timeout)
			return data
			
		except asyncio.TimeoutError:
			self.pending_requests.pop('CNSRLST', None)
			print('조건식 목록 조회 시간 초과')
			return None
		except Exception as e:
			self.pending_requests.pop('CNSRLST', None)
			print(f'조건식 목록 조회 실패: {e}')
			return None

	async def start_feature_1(self):
		"""기능 1 (조건식 검색) 시작"""
		self.feature_1_active = True
		await self._register_conditions()

	async def stop_feature_1(self):
		"""기능 1 (조건식 검색) 중지"""
		self.feature_1_active = False
		await self._clear_all_conditions()
	
	async def start_feature_2(self):
		"""기능 2 (익절/손절) 시작: Portfolio 초기화 및 0B 등록"""
		self.feature_2_active = True
		
		# Portfolio 초기화
		success = await self._initialize_portfolio()
		if not success:
			self.feature_2_active = False
			return False
		
		# 웹소켓이 연결되어 있고 로그인된 상태면 0B 등록
		if self.connected:
			await self._register_stock_quotes()
		
		return True
	
	async def stop_feature_2(self):
		"""기능 2 (익절/손절) 중지"""
		self.feature_2_active = False
		# Portfolio는 유지 (기능 5가 활성화되어 있을 수 있음)
	
	async def start_feature_5(self):
		"""기능 5 (트레일링 스탑) 시작: Portfolio 초기화 및 0B 등록"""
		self.feature_5_active = True
		
		# Portfolio 초기화
		success = await self._initialize_portfolio()
		if not success:
			self.feature_5_active = False
			return False
		
		# 웹소켓이 연결되어 있고 로그인된 상태면 0B 등록
		if self.connected:
			await self._register_stock_quotes()
		
		return True
	
	async def stop_feature_5(self):
		"""기능 5 (트레일링 스탑) 중지"""
		self.feature_5_active = False
		# Portfolio는 유지 (기능 2가 활성화되어 있을 수 있음)
	
	async def start_feature_6(self):
		"""기능 6 (돌파 매수) 시작: 감시 목록 초기화 및 0B 등록"""
		self.feature_6_active = True
		
		# 보유 종목 동기화 (중복 매수 방지)
		await self._initialize_portfolio()
		
		success = await self._register_breakout_stocks()
		if not success:
			self.feature_6_active = False
			return False
		return True
	
	async def stop_feature_6(self):
		"""기능 6 (돌파 매수) 중지"""
		self.feature_6_active = False
		await self._cleanup_breakout_stocks()
	
	async def update_conditions(self, new_seqs):
		"""
		조건식을 변경합니다. 기존 조건식들을 모두 해제하고 새로운 조건식들을 등록합니다.
		
		Args:
			new_seqs: 새로운 조건식 번호 리스트
		"""
		# 기존 조건식들 모두 해제
		await self._clear_all_conditions()
		
		# 새로운 조건식들 등록
		if self.feature_1_active:
			await asyncio.sleep(0.5)  # 해제 후 잠시 대기
			for seq in new_seqs:
				await self.send_message({ 
					'trnm': 'CNSRREQ',
					'seq': str(seq),
					'search_type': '1',
					'stex_tp': 'K',
				}, self.token)
				self.registered_seqs.add(str(seq))
				print(f'조건식 등록: seq {seq}')
				
				# 재로그인 중이 아닐 때만 각 조건식 등록을 텔레그램으로 알림
				if not self.is_relogging:
					await tel_send(f"📊 조건식 등록: {seq}번")
				
				# 서버 부하 방지 및 안정적인 처리 보장을 위해 0.2초 대기
				await asyncio.sleep(0.2)
			
			print(f'조건식 변경 완료. 새로운 조건식: {", ".join(map(str, new_seqs))}')
	
	# ========== Portfolio Manager 관련 메서드 ==========
	
	def _save_trailing_high_price(self, stock_code, high_price):
		"""특정 종목의 트레일링 스탑 고점을 영구 저장합니다."""
		try:
			# 기존 데이터 로드
			trailing_status = _load_trailing_status()
			
			# 현재 시각 기록
			now = datetime.datetime.now()
			timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
			
			# 종목 코드 정규화 (일관성 유지)
			stk_cd_clean = normalize_stock_code(stock_code)
			
			# 고점 정보 업데이트
			trailing_status[stk_cd_clean] = {
				'high_price': high_price,
				'timestamp': timestamp
			}
			
			# 저장
			_save_trailing_status(trailing_status)
		except Exception as e:
			print(f"트레일링 스탑 고점 저장 중 오류: {e}")
	
	async def _initialize_portfolio(self):
		"""Portfolio 초기화: fn_kt00004를 호출하여 보유 종목 정보를 로드합니다."""
		max_retries = 3
		retry_delays = [1, 2, 4]  # 지수 백오프
		
		for attempt in range(max_retries):
			try:
				print(f"Portfolio 초기화 시도 {attempt + 1}/{max_retries}...")
				my_stocks = await fn_kt00004(False, 'N', '', self.token)
				
				if not my_stocks:
					print("보유 종목이 없습니다.")
					self.portfolio = {}
					return True
				
				# Portfolio 초기화
				self.portfolio = {}
				for stock in my_stocks:
					stk_cd = stock.get('stk_cd', '')
					stk_cd_clean = normalize_stock_code(stk_cd)
					
					if not stk_cd_clean:
						continue
					
					# 평균단가
					avg_prc = stock.get('avg_prc', '0')
					try:
						# 콤마 제거 후 변환
						avg_prc_str = str(avg_prc).replace(',', '').strip() if avg_prc else '0'
						avg_price = float(avg_prc_str) if avg_prc_str else 0.0
					except (ValueError, TypeError):
						avg_price = 0.0
					
					# 보유수량
					rmnd_qty = stock.get('rmnd_qty', '0')
					try:
						rmnd_qty_str = str(rmnd_qty).replace(',', '').strip() if rmnd_qty else '0'
						quantity = int(rmnd_qty_str) if rmnd_qty_str else 0
					except (ValueError, TypeError):
						quantity = 0
					
					if quantity <= 0:
						continue
					
					# 평균단가가 0 이하이고 보유수량이 0보다 큰 경우, 매입금액으로 역산
					if avg_price <= 0 and quantity > 0:
						pchs_amt = stock.get('pchs_amt', '0')
						try:
							# 콤마 제거 후 변환
							pchs_amt_str = str(pchs_amt).replace(',', '').strip() if pchs_amt else '0'
							pchs_amt_float = float(pchs_amt_str) if pchs_amt_str else 0.0
							if pchs_amt_float > 0:
								avg_price = pchs_amt_float / quantity
								print(f"평균단가 역산 완료: {stock.get('stk_nm', stk_cd_clean)} ({stk_cd_clean}) - 매입금액 {pchs_amt_float:,.0f}원 / 수량 {quantity}주 = {avg_price:,.0f}원")
						except (ValueError, TypeError, ZeroDivisionError) as e:
							print(f"평균단가 역산 실패 ({stk_cd_clean}): {e}")
							avg_price = 0.0
					
					# 현재가 초기화
					high_price = avg_price  # 기본값: 평단가
					
					# now_prc 또는 prst_prc 필드 확인
					now_prc = stock.get('now_prc') or stock.get('prst_prc')
					if now_prc:
						try:
							# 문자열에서 부호 제거 및 변환
							now_prc_str = str(now_prc).replace('+', '').replace('-', '')
							high_price = float(now_prc_str) if now_prc_str else avg_price
						except (ValueError, TypeError):
							pass
					
					# 역산 시도 (evlu_amt / rmnd_qty)
					if high_price == avg_price and avg_price == 0:
						evlu_amt = stock.get('evlu_amt', '0')
						try:
							evlu_amt_float = float(evlu_amt) if evlu_amt else 0.0
							if evlu_amt_float > 0 and quantity > 0:
								high_price = evlu_amt_float / quantity
						except (ValueError, TypeError):
							pass
					
					# Portfolio에 추가
					self.portfolio[stk_cd_clean] = {
						'name': stock.get('stk_nm', stk_cd_clean),
						'avg_price': avg_price,
						'quantity': quantity,
						'high_price': high_price,
						'status': 'HOLDING',
						'last_update_time': time.time()
					}
				
				# trailing_status.json에서 저장된 고점 정보 로드 및 복원
				trailing_status = _load_trailing_status()
				if trailing_status:
					restored_count = 0
					for stock_code, stock_data in self.portfolio.items():
						if stock_code in trailing_status:
							saved_info = trailing_status[stock_code]
							saved_high_price = saved_info.get('high_price', 0)
							
							# 저장된 고점이 현재 초기화된 고점보다 높으면 복원
							if saved_high_price > stock_data['high_price']:
								stock_data['high_price'] = saved_high_price
								timestamp = saved_info.get('timestamp', '알 수 없음')
								print(f"  ✓ {stock_data['name']} ({stock_code}): 고점 복원 {saved_high_price:,.0f}원 (저장 시각: {timestamp})")
								restored_count += 1
					
					if restored_count > 0:
						print(f"트레일링 스탑 고점 복원 완료: {restored_count}개 종목")
				
				print(f"Portfolio 초기화 완료: {len(self.portfolio)}개 종목")
				return True
				
			except Exception as e:
				print(f"Portfolio 초기화 실패 (시도 {attempt + 1}/{max_retries}): {e}")
				if attempt < max_retries - 1:
					await asyncio.sleep(retry_delays[attempt])
				else:
					# 최종 실패 시 텔레그램 알림
					await tel_send(f"❌ Portfolio 초기화 실패: {str(e)}")
					return False
		
		return False
	
	async def _register_breakout_stocks(self):
		"""기능 6 감시 목록을 불러와 전일 종가를 저장하고 0B를 등록합니다."""
		try:
			break_list = get_setting('break_stock_list', [])
			if not isinstance(break_list, list):
				break_list = []
			
			break_list = [code.strip() for code in break_list if code and str(code).strip()]
			break_list = list(dict.fromkeys(break_list))  # 중복 제거, 순서 유지
			
			if len(break_list) == 0:
				await tel_send("⚠️ 돌파 감시 목록이 비어 있어 기능 6을 시작할 수 없습니다.")
				return False
			
			# 토큰 확보
			if not self.token:
				self.token = await self.token_manager.get_token()
			if not self.token:
				await tel_send("❌ 돌파 매수 감시를 위한 토큰 발급에 실패했습니다.")
				return False
			
			self.break_stock_list = set(break_list)
			
			# 전일 종가 조회 및 Portfolio 반영
			for code in break_list:
				try:
					info = await stock_info(code, token=self.token)
				except Exception as e:
					print(f"전일 종가 조회 실패({code}): {e}")
					info = {}
				
				name = info.get('stk_nm', code) if isinstance(info, dict) else code
				prev_close_raw = None
				if isinstance(info, dict):
					prev_close_raw = (
						info.get('pred_close_pric') or
						info.get('prev_close_price') or
						info.get('prdy_clpr') or
						info.get('bfdy_clpr')
					)
				
				prev_close = 0.0
				if prev_close_raw is not None:
					try:
						prev_close_str = str(prev_close_raw).replace('+', '').replace('-', '')
						prev_close = float(prev_close_str) if prev_close_str else 0.0
					except (ValueError, TypeError):
						prev_close = 0.0
				
				stock_data = self.portfolio.get(code, {})
				stock_data['name'] = stock_data.get('name') or name
				stock_data['avg_price'] = stock_data.get('avg_price', 0.0)
				stock_data['quantity'] = stock_data.get('quantity', 0)
				stock_data['high_price'] = stock_data.get('high_price', prev_close or 0.0)
				stock_data['status'] = stock_data.get('status', 'WATCH')
				stock_data['prev_close_price'] = prev_close
				stock_data['watch_breakout'] = True
				stock_data['last_update_time'] = stock_data.get('last_update_time') or time.time()
				self.portfolio[code] = stock_data
				
				# API 제한 보호
				await asyncio.sleep(0.2)
			
			# 감시 종목 0B 등록
			await self._register_stock_quotes()
			
			if not self.is_relogging:
				await tel_send(f"✅ 돌파 매수 감시 목록 등록 완료: {', '.join(break_list)}")
			return True
		except Exception as e:
			print(f"돌파 감시 목록 등록 중 오류: {e}")
			if not self.is_relogging:
				await tel_send(f"❌ 돌파 감시 목록 등록 중 오류가 발생했습니다: {e}")
			return False
	
	async def _cleanup_breakout_stocks(self):
		"""기능 6 중지 시 감시 종목의 실시간 해제 및 정리"""
		codes = list(self.break_stock_list)
		self.break_stock_list.clear()
		
		for code in codes:
			stock_data = self.portfolio.get(code)
			# 기능 2/5와 공유하며 실제 보유 중이면 해제하지 않음
			if (self.feature_2_active or self.feature_5_active) and stock_data and stock_data.get('quantity', 0) > 0:
				if stock_data.get('watch_breakout'):
					stock_data['watch_breakout'] = False
				continue
			
			try:
				await self._unregister_stock_quote(code)
			except Exception as e:
				print(f"돌파 감시 해제 실패({code}): {e}")
			
			if stock_data and stock_data.get('quantity', 0) > 0:
				stock_data['watch_breakout'] = False
			else:
				self.portfolio.pop(code, None)
	async def _register_stock_quotes(self, force_refresh=False):
		"""
		Portfolio의 모든 종목에 대해 0B (주식체결) 실시간 등록
		
		Args:
			force_refresh: True이면 기존 등록 상태를 무시하고 refresh='0'으로 초기화
		"""
		stock_codes = []
		
		# Portfolio 종목 추가
		if self.portfolio:
			stock_codes.extend(list(self.portfolio.keys()))
		
		if not stock_codes:
			return
		
		try:
			# === 중복 등록 방지: 이미 등록된 종목 필터링 ===
			current_time = time.time()
			new_stock_codes = []
			
			for stock_code in stock_codes:
				# force_refresh이면 모든 종목 등록
				if force_refresh:
					new_stock_codes.append(stock_code)
					continue
				
				# 등록 상태 확인
				registered_info = self.registered_items.get(stock_code)
				if registered_info:
					# 최근 5초 이내 등록된 종목은 스킵 (중복 방지)
					last_registered = registered_info.get('last_registered', 0)
					if current_time - last_registered < 5.0:
						print(f"  ⏭️ {stock_code}: 최근 등록되어 있어 스킵합니다.")
						continue
				
				new_stock_codes.append(stock_code)
			
			if not new_stock_codes:
				print(f"[OB 등록] 모든 종목이 이미 등록되어 있습니다. 스킵합니다.")
				return
			
			# 등록 전 종목별 정보 출력
			feature_type = []
			if self.feature_2_active:
				feature_type.append("익절/손절")
			if self.feature_5_active:
				feature_type.append("트레일링 스탑")
			if self.feature_6_active:
				feature_type.append("돌파 매수")
			feature_str = " + ".join(feature_type) if feature_type else "알 수 없음"
			
			portfolio_cnt = len(self.portfolio) if self.portfolio else 0
			print(
				f"\n[OB 등록] {feature_str} 감시를 위해 {len(new_stock_codes)}개 종목 등록 "
				f"(전체 {len(stock_codes)}개 중, Portfolio {portfolio_cnt}):"
			)
			for stock_code in new_stock_codes:
				stock_data = self.portfolio.get(stock_code)
				
				# 포트폴리오에 존재 (보유/관리 중인 종목)
				if stock_data is not None:
					name = stock_data.get('name', stock_code)
					avg_price = stock_data.get('avg_price', 0) or 0
					quantity = stock_data.get('quantity', 0) or 0
					high_price = stock_data.get('high_price', 0) or 0
					
					# 로그 메시지
					if quantity > 0:
						print(f"  - {name} ({stock_code}): 평균단가 {avg_price:,.0f}원, 수량 {quantity}주, 초기고점 {high_price:,.0f}원")
					else:
						print(f"  - {name} ({stock_code}): [미보유] 현재가 모니터링 중")
			
			# === 디바운싱: 요청을 큐에 추가하고 배치로 처리 ===
			await self._queue_reg_request(new_stock_codes, ['0B'], force_refresh)
			
		except Exception as e:
			print(f"0B 실시간 등록 실패: {e}")
	
	async def _queue_reg_request(self, stock_codes, item_type, force_refresh=False):
		"""
		REG 요청을 디바운싱 큐에 추가합니다.
		
		Args:
			stock_codes: 등록할 종목 코드 리스트
			item_type: 항목 타입 리스트 (예: ['0B'])
			force_refresh: True이면 refresh='0'으로 초기화
		"""
		async with self._reg_debounce_lock:
			# 큐에 추가
			self._pending_reg_requests.append({
				'stock_codes': stock_codes,
				'item_type': item_type,
				'force_refresh': force_refresh,
				'timestamp': time.time()
			})
			
			# 디바운싱 태스크가 없으면 시작
			if self._reg_debounce_task is None or self._reg_debounce_task.done():
				self._reg_debounce_task = asyncio.create_task(self._process_reg_debounce())
	
	async def _process_reg_debounce(self):
		"""디바운싱: 일정 시간 대기 후 모든 대기 중인 REG 요청을 배치로 처리"""
		try:
			# 디바운싱 딜레이 대기
			await asyncio.sleep(self._reg_debounce_delay)
			
			async with self._reg_debounce_lock:
				if not self._pending_reg_requests:
					return
				
				# 모든 대기 중인 요청을 하나로 병합
				all_stock_codes = []
				item_type = ['0B']  # 기본값
				force_refresh = False
				
				for req in self._pending_reg_requests:
					all_stock_codes.extend(req['stock_codes'])
					item_type = req['item_type']  # 마지막 요청의 타입 사용
					if req['force_refresh']:
						force_refresh = True
				
				# 중복 제거
				all_stock_codes = list(dict.fromkeys(all_stock_codes))
				
				# 큐 비우기
				self._pending_reg_requests.clear()
			
			# 실제 REG 전송
			if all_stock_codes:
				await self._send_reg_message(all_stock_codes, item_type, force_refresh)
		except Exception as e:
			print(f"[REG 디바운싱] 오류: {e}")
	
	async def _send_reg_message(self, stock_codes, item_type, force_refresh=False):
		"""
		실제 REG 메시지를 전송합니다.
		
		Args:
			stock_codes: 등록할 종목 코드 리스트
			item_type: 항목 타입 리스트 (예: ['0B'])
			force_refresh: True이면 refresh='0'으로 초기화
		"""
		# 백오프 체크: 에러 1700 발생 시 일정 시간 대기
		current_time = time.time()
		if current_time < self._reg_backoff_until:
			remaining = self._reg_backoff_until - current_time
			print(f"⚠️ [REG 백오프] {remaining:.1f}초 후 재시도합니다.")
			return
		
		# 백오프 시간이 지났으면 에러 카운트 리셋
		if self._reg_error_count > 0 and current_time >= self._reg_backoff_until:
			self._reg_error_count = 0
			self._reg_backoff_duration = 5.0  # 기본값으로 복원
			print(f"✅ [REG] 백오프 해제. 정상 요청을 재개합니다.")
		
		if not stock_codes:
			return
		
		try:
			# 5개씩 나누어 등록 (API 길이 제한 100자 고려)
			chunk_size = 5
			first_chunk = True
			
			for i in range(0, len(stock_codes), chunk_size):
				chunk = stock_codes[i:i + chunk_size]
				
				# refresh: force_refresh이거나 첫 번째 분할은 '0'(초기화), 이후는 '1'(추가)
				if force_refresh and first_chunk:
					refresh = '0'
					first_chunk = False
				else:
					refresh = '0' if i == 0 and force_refresh else '1'
				
				# REG 메시지 전송
				await self.send_message({
					'trnm': 'REG',
					'grp_no': '1',  # 그룹 번호
					'refresh': refresh,
					'data': [{
						'type': item_type,
						'item': chunk
					}]
				}, self.token)
				print(f"0B 실시간 등록 요청 (분할 {i//chunk_size + 1}, refresh={refresh}): {', '.join(chunk)}")
				
				# 연속 요청 시 서버 부하 방지 및 처리 보장을 위해 잠시 대기
				await asyncio.sleep(0.3)
			
			print(f"0B 실시간 등록 완료: 총 {len(stock_codes)}개 종목\n")
		except Exception as e:
			print(f"0B 실시간 등록 실패: {e}")
	
	async def _register_order_execution(self):
		"""
		주문체결(00) 실시간 알림을 등록합니다.
		이 요청을 보내야 주문 접수/체결 시 서버로부터 00 메시지가 수신됩니다.
		"""
		try:
			# 백오프 체크
			current_time = time.time()
			if current_time < self._reg_backoff_until:
				remaining = self._reg_backoff_until - current_time
				print(f"⚠️ [REG 백오프] 주문체결 등록을 {remaining:.1f}초 후 재시도합니다.")
				return
			
			print("[알림] 주문체결(00) 실시간 등록을 요청합니다.")
			await self.send_message({
				'trnm': 'REG',
				'grp_no': '1',       # 그룹 번호
				'refresh': '1',      # 1: 추가 등록 (0으로 하면 기존 감시 종목들이 해제될 수 있음)
				'data': [{
					'type': ['00'],  # 실시간 항목: 주문체결
					'item': ['']     # 주문체결은 종목코드가 아닌 계좌 단위이므로 빈 값 전송 
				}]
			}, self.token)
		except Exception as e:
			print(f"주문체결 실시간 등록 실패: {e}")
	
	async def _unregister_stock_quote(self, stock_code):
		"""특정 종목의 0B 실시간 해제"""
		try:
			await self.send_message({
				'trnm': 'REMOVE',
				'grp_no': '1',  # 그룹 번호
				'data': [{
					'type': ['0B'],  # 수정: ['0B'] (리스트 형태)
					'item': [stock_code]
				}]
			}, self.token)
			print(f"0B 실시간 해제 완료: {stock_code}")
		except Exception as e:
			print(f"0B 실시간 해제 실패: {e}")
	
	async def _sync_portfolio_task(self):
		"""
		10초 주기로 실제 잔고(kt00004)와 self.portfolio를 동기화합니다.
		- 누락 종목: real_stocks에는 있으나 portfolio에는 없음 -> portfolio 추가 + REG(0B) 복구
		- 좀비 종목: last_update_time이 60초 이상 경과 -> REG(0B) 복구
		- 유령 종목: portfolio에는 있으나 real_stocks에는 없음(이미 매도됨) -> portfolio에서 삭제
		"""
		def _to_float(value, default: float = 0.0) -> float:
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
				s = s.replace('+', '').replace('-', '')
				return float(s) if s else default
			except Exception:
				return default

		def _add_reg_target(reg_targets: List[str], reg_set: set, code: str):
			if code and code not in reg_set:
				reg_set.add(code)
				reg_targets.append(code)

		try:
			while self.keep_running:
				# API 과부하 방지: 10초 주기 준수
				await asyncio.sleep(10)

				# 재로그인 중/토큰 부재 시 이번 회차 스킵
				if self.is_relogging or not self.token:
					continue

				# 1) 실제 잔고 조회
				try:
					real_stocks = await fn_kt00004(False, 'N', '', self.token)
				except Exception as e:
					print(f"[동기화] 잔고 조회 실패: {e}")
					continue

				# None/빈 리스트면 섣불리 비우지 않고 이번 회차 스킵
				if not real_stocks:
					continue

				real_map: Dict[str, Dict] = {}
				for stock in real_stocks:
					if not isinstance(stock, dict):
						continue
					stk_cd = str(stock.get('stk_cd', '') or '')
					stk_cd_clean = normalize_stock_code(stk_cd)
					if not stk_cd_clean:
						continue

					quantity = _to_int(stock.get('rmnd_qty', '0'), 0)
					if quantity <= 0:
						continue

					avg_price = _to_float(stock.get('avg_prc', '0'), 0.0)
					
					# 평균단가가 0 이하이고 보유수량이 0보다 큰 경우, 매입금액으로 역산
					if avg_price <= 0 and quantity > 0:
						pchs_amt = stock.get('pchs_amt', '0')
						try:
							# 콤마 제거 후 변환
							pchs_amt_str = str(pchs_amt).replace(',', '').strip() if pchs_amt else '0'
							pchs_amt_float = float(pchs_amt_str) if pchs_amt_str else 0.0
							if pchs_amt_float > 0:
								avg_price = pchs_amt_float / quantity
								print(f"[동기화] 평균단가 역산 완료: {stock.get('stk_nm', stk_cd_clean)} ({stk_cd_clean}) - 매입금액 {pchs_amt_float:,.0f}원 / 수량 {quantity}주 = {avg_price:,.0f}원")
						except (ValueError, TypeError, ZeroDivisionError) as e:
							print(f"[동기화] 평균단가 역산 실패 ({stk_cd_clean}): {e}")
							avg_price = 0.0
					
					now_prc = stock.get('now_prc') or stock.get('prst_prc')
					current_price = _to_float(now_prc, avg_price if avg_price > 0 else 0.0)
					if current_price <= 0:
						current_price = avg_price if avg_price > 0 else 0.0

					real_map[stk_cd_clean] = {
						'name': stock.get('stk_nm', stk_cd_clean),
						'avg_price': avg_price,
						'quantity': quantity,
						'current_price': current_price,
					}

				# 유효한 보유 종목이 없으면 이번 회차 스킵(이상치 방어)
				if not real_map:
					# 보유 종목이 없어도 이전 목록과 비교하여 매도 완료 알림
					current_stocks = set()
					if self.previous_held_stocks:
						sold_stocks = self.previous_held_stocks - current_stocks
						for code in sold_stocks:
							stock_data = self.portfolio.get(code, {})
							name = stock_data.get('name', code) if stock_data else code
							message = f"🔵 {name} ({code}) 매도 완료"
							await tel_send(message)
							print(f"[동기화] {message}")
					self.previous_held_stocks = current_stocks
					continue

				# 현재 보유 종목 목록
				current_stocks = set(real_map.keys())
				
				# 이전 보유 종목 목록과 비교하여 매수/매도 완료 알림
				if self.previous_held_stocks:
					# 추가된 종목 (매수 완료)
					bought_stocks = current_stocks - self.previous_held_stocks
					for code in bought_stocks:
						info = real_map.get(code, {})
						name = info.get('name', code)
						quantity = info.get('quantity', 0)
						avg_price = info.get('avg_price', 0.0)
						message = f"✅ {name} ({code}) {quantity}주 매수 완료 (평단가: {avg_price:,.0f}원)"
						await tel_send(message)
						print(f"[동기화] {message}")
					
					# 빠진 종목 (매도 완료)
					sold_stocks = self.previous_held_stocks - current_stocks
					for code in sold_stocks:
						stock_data = self.portfolio.get(code, {})
						name = stock_data.get('name', code) if stock_data else code
						message = f"✅ {name} ({code}) 매도 완료"
						await tel_send(message)
						print(f"[동기화] {message}")
				
				# 현재 보유 종목 목록을 이전 목록으로 업데이트
				self.previous_held_stocks = current_stocks.copy()

				# 보유 중인 종목들의 마지막 보유 시간 업데이트 (10초마다)
				# 다른 프로그램에서 매도한 경우도 감지하기 위해 보유 중인 종목만 업데이트
				for code in real_map.keys():
					try:
						update_last_held_time(code)
					except Exception as e:
						# 오류가 발생해도 동기화는 계속 진행
						print(f"[동기화] 마지막 보유 시간 업데이트 실패 ({code}): {e}")

				# 2) 누락/좀비/유령 탐색
				now_ts = time.time()
				reg_targets: List[str] = []
				reg_set = set()

				# 누락 종목: real_map에는 있으나 portfolio에는 없음 -> 추가 + REG
				for code, info in real_map.items():
					if code not in self.portfolio:
						self.portfolio[code] = {
							'name': info.get('name', code),
							'avg_price': float(info.get('avg_price', 0.0) or 0.0),
							'quantity': int(info.get('quantity', 0) or 0),
							'high_price': float(info.get('current_price', 0.0) or 0.0),
							'status': 'HOLDING',
							'last_update_time': now_ts
						}
						_add_reg_target(reg_targets, reg_set, code)
					else:
						# 최소 필드 보정(동기화 안정성)
						stock_data = self.portfolio.get(code, {})
						stock_data['name'] = stock_data.get('name') or info.get('name', code)
						stock_data['avg_price'] = float(info.get('avg_price', stock_data.get('avg_price', 0.0)) or 0.0)
						stock_data['quantity'] = int(info.get('quantity', stock_data.get('quantity', 0)) or 0)
						if not stock_data.get('high_price'):
							stock_data['high_price'] = float(info.get('current_price', 0.0) or 0.0)
						stock_data['last_update_time'] = stock_data.get('last_update_time') or now_ts
						self.portfolio[code] = stock_data

				# 유령 종목: portfolio에는 있으나 real_map에는 없음(이미 매도됨) -> 제거
				# NOTE: 돌파/기타 미보유 감시 종목(watch_breakout 등)은 유지
				for code in list((self.portfolio or {}).keys()):
					if code in real_map:
						continue
					stock_data = self.portfolio.get(code, {})
					if stock_data.get('watch_breakout'):
						continue
					quantity = _to_int(stock_data.get('quantity', 0), 0)
					status = str(stock_data.get('status', '') or '')
					if quantity > 0 or status in ('HOLDING', 'ORDERING'):
						self.portfolio.pop(code, None)

				# 좀비 종목: 60초 이상 시세 미수신 -> REG
				for code, stock_data in (self.portfolio or {}).items():
					last_update = stock_data.get('last_update_time', 0) or 0
					try:
						last_update = float(last_update)
					except Exception:
						last_update = 0.0
					if now_ts - last_update >= 60:
						_add_reg_target(reg_targets, reg_set, code)

				# 3) 복구 실행 (REG 전송) - 디바운싱 적용
				if reg_targets:
					try:
						# 디바운싱을 통해 배치로 처리
						await self._queue_reg_request(reg_targets, ['0B'], force_refresh=False)
						print(f"[동기화] {len(reg_targets)}개 종목 REG 복구 요청 큐에 추가됨")
					except Exception as e:
						print(f"[동기화] REG 복구 전송 실패: {e}")

		except asyncio.CancelledError:
			print("잔고 동기화 태스크가 중지되었습니다")
			raise
		except Exception as e:
			print(f"잔고 동기화 태스크 오류: {e}")

	async def _handle_stock_quote(self, response):
		"""0B (주식체결) 메시지 처리: 현재가 감시 및 매도/매수 판단."""
		# Phase 2 Step C: 보유 종목 손절 모니터링은 features 활성화와 무관하게 동작
		holdings_mgr = getattr(self, 'holdings_manager', None)
		if holdings_mgr is not None:
			try:
				code_raw = response.get('item', '')
				if isinstance(code_raw, list):
					code_raw = code_raw[0] if code_raw else ''
				v = response.get('values', {}) or {}
				cur_raw = str(v.get('10', '0')).replace('+', '').replace('-', '').strip()
				if code_raw and cur_raw:
					try:
						current = int(float(cur_raw))
					except (ValueError, TypeError):
						current = 0
					if current > 0:
						asyncio.create_task(holdings_mgr.on_0b_quote(code_raw, current))
			except Exception:
				logger.exception("holdings_manager dispatch error")

		if not (self.feature_2_active or self.feature_5_active or self.feature_6_active):
			return

		try:
			stock_code = response.get('item', '')
			# item이 리스트인 경우 첫 번째 요소 사용
			if isinstance(stock_code, list):
				stock_code = stock_code[0] if stock_code else ''
			value = response.get('values', {})
			
			if not stock_code or not value:
				return
			
			# Portfolio에서 종목 조회
			stock_data = self.portfolio.get(stock_code)
			if not stock_data:
				return
			
			# 시세 수신 타임스탬프 기록 (Fail-safe 동기화용)
			stock_data['last_update_time'] = time.time()
			
			# 현재가 파싱
			current_price_str = value.get('10', '0')
			try:
				# 부호 제거 및 변환
				current_price_str = str(current_price_str).replace('+', '').replace('-', '')
				current_price = float(current_price_str) if current_price_str else 0.0
			except (ValueError, TypeError):
				return
			
			if current_price <= 0:
				return
			
			# 기능 6: 돌파 매수 체크
			if self.feature_6_active and stock_data.get('watch_breakout'):
				await self._handle_breakout_signal(stock_code, stock_data, current_price)
			
			# 기능 2/5가 비활성화되어 있으면 종료
			if not (self.feature_2_active or self.feature_5_active):
				return
			
			# 보유 수량이 없으면 매도 로직 건너뜀 및 portfolio에서 제거
			if stock_data.get('quantity', 0) <= 0:
				# portfolio에서 제거하고 0B 등록 해제 (sell 명령어로 매도했지만 04 메시지가 아직 오지 않은 경우)
				if stock_code in self.portfolio:
					del self.portfolio[stock_code]
					await self._unregister_stock_quote(stock_code)
					print(f"보유 수량이 0이므로 portfolio에서 제거: {stock_code}")
				return
			
			# ORDERING 상태면 추가 처리 없음
			if stock_data.get('status') == 'ORDERING':
				return
			
			# 고점 갱신 (트레일링 스탑용)
			if current_price > stock_data.get('high_price', 0):
				stock_data['high_price'] = current_price
				print(f"{stock_data.get('name', stock_code)} ({stock_code}): 고점 갱신 {current_price:,.0f}원")
				# 고점 갱신 시 영구 저장
				self._save_trailing_high_price(stock_code, current_price)
			
			# 수익률 계산
			avg_price = stock_data.get('avg_price', 0)
			if avg_price <= 0:
				return
			
			current_profit_rate = ((current_price - avg_price) / avg_price) * 100
			
			# 트레일링 스탑 하락률 계산
			high_price = stock_data.get('high_price', 0)
			drop_rate = ((high_price - current_price) / high_price) * 100 if high_price > 0 else 0
			
			# 매도 판단 (OR 조건)
			should_sell = False
			reason = ""
			
			# 기능 2: 익절/손절 — 종목별 tpr/slr override 우선 (stick), 없으면 글로벌
			if self.feature_2_active:
				from utils.holdings import get_holding_override
				_ov = get_holding_override(stock_code)
				TP_RATE = _ov['tpr'] if _ov['tpr'] is not None else cached_setting('take_profit_rate', 10.0)
				SL_RATE = _ov['slr'] if _ov['slr'] is not None else cached_setting('stop_loss_rate', -10.0)

				if current_profit_rate >= TP_RATE or current_profit_rate <= SL_RATE:
					should_sell = True
					reason = "익절" if current_profit_rate >= TP_RATE else "손절"
			
			# 기능 5: 트레일링 스탑
			if not should_sell and self.feature_5_active:
				TRAILING_STOP_RATE = cached_setting('trailing_stop_rate', 3.0)
				trailing_min_profit = cached_setting('trailing_min_profit', 0.0)
				min_profit_ok = current_profit_rate >= trailing_min_profit
				if drop_rate >= TRAILING_STOP_RATE and min_profit_ok:
					should_sell = True
					reason = "트레일링 스탑"
			
			# 매도 조건이 아니더라도 현재 감시 상태를 출력 (사용자 확인용)
			# 단, 3초 쿨타임 적용하여 과도한 로그 출력 방지
			if not should_sell:
				current_time = time.time()
				last_log_time = self.last_log_time.get(stock_code, 0)
				
				# 3초 쿨타임 체크: (현재시간 - 마지막출력시간) > 3초인 경우에만 출력
				if current_time - last_log_time > 3.0:
					# 수익률에 따라 색상 다르게 표시 (이모지 활용)
					emoji = "🔴" if current_profit_rate > 0 else "🔵"
					print(f"👀 [감시] {stock_data.get('name', stock_code)} | 현재가: {current_price:,.0f} | 수익률: {emoji} {current_profit_rate:.2f}% | 고점대비: -{drop_rate:.2f}%")
					# 마지막 출력 시간 갱신
					self.last_log_time[stock_code] = current_time
			
			# 신호 수신 시 상세 정보 출력
			if should_sell:
				print(f"\n[OB 신호 수신] {stock_data.get('name', stock_code)} ({stock_code})")
				print(f"  현재가: {current_price:,.0f}원")
				print(f"  평균단가: {avg_price:,.0f}원")
				print(f"  수익율 계산: (({current_price:,.0f} - {avg_price:,.0f}) / {avg_price:,.0f}) * 100 = {current_profit_rate:.2f}%")
				print(f"  고점: {high_price:,.0f}원")
				if self.feature_5_active:
					print(f"  트레일링 하락률 계산: (({high_price:,.0f} - {current_price:,.0f}) / {high_price:,.0f}) * 100 = {drop_rate:.2f}%")
				if self.feature_2_active:
					from utils.holdings import get_holding_override
					_ov = get_holding_override(stock_code)
					TP_RATE = _ov['tpr'] if _ov['tpr'] is not None else cached_setting('take_profit_rate', 10.0)
					SL_RATE = _ov['slr'] if _ov['slr'] is not None else cached_setting('stop_loss_rate', -10.0)
					ov_tag = " (stick override)" if (_ov['tpr'] is not None or _ov['slr'] is not None) else ""
					print(f"  익절 기준: {TP_RATE}%, 손절 기준: {SL_RATE}%{ov_tag}")
				if self.feature_5_active:
					TRAILING_STOP_RATE = cached_setting('trailing_stop_rate', 3.0)
					print(f"  트레일링 스탑 기준: {TRAILING_STOP_RATE}%")
					trailing_min_profit = cached_setting('trailing_min_profit', 0.0)
					min_profit_ok = current_profit_rate >= trailing_min_profit
					print(f"  최소 발동 수익률: {trailing_min_profit}% (충족: {'Y' if min_profit_ok else 'N'})")
				print(f"  매도 판단: {reason} 조건 충족\n")
			
			# 매도 실행
			if should_sell:
				await self._execute_sell_order(stock_code, stock_data, current_price, current_profit_rate, reason)
		
		except Exception as e:
			print(f"주식체결 메시지 처리 중 오류: {e}")
	
	async def _handle_breakout_signal(self, stock_code, stock_data, current_price):
		"""기능 6: 돌파 매수 조건을 확인하고 주문 실행"""
		try:
			if stock_code not in self.break_stock_list:
				return
			
			if stock_data.get('status') == 'ORDERING':
				return
			
			# 이미 보유 중이면 스킵
			if stock_data.get('quantity', 0) > 0:
				return
			
			prev_close = stock_data.get('prev_close_price', 0)
			if prev_close <= 0:
				return
			
			break_rate = cached_setting('break_rate', 3.0)
			increase_rate = ((current_price - prev_close) / prev_close) * 100
			
			if increase_rate < break_rate:
				return
			
			await self._execute_buy_order_breakout(stock_code, stock_data, current_price, increase_rate)
		except Exception as e:
			print(f"돌파 매수 체크 중 오류({stock_code}): {e}")
	
	async def _execute_buy_order_breakout(self, stock_code, stock_data, current_price, increase_rate):
		"""기능 6: 돌파 매수 주문 실행"""
		try:
			# 토큰 확인
			if not self.token:
				self.token = await self.token_manager.get_token()
			if not self.token:
				print("토큰이 없어 돌파 매수를 건너뜁니다.")
				return
			
			# 재매수 쿨다운 확인
			cooldown_hours = cached_setting('sell_cooldown_hours', 24)
			if is_in_cooldown(stock_code, cooldown_hours):
				remaining = get_cooldown_remaining(stock_code, cooldown_hours)
				print(f"{stock_code} 매도 쿨다운 남음: {remaining:.1f}시간")
				return
			
			# 보유종목 개수 제한 확인
			max_holdings = cached_setting('max_holdings', 0)
			holding_count = len([c for c, d in self.portfolio.items() if d.get('quantity', 0) > 0])
			if max_holdings > 0 and holding_count >= max_holdings:
				print(f"돌파 매수 제한: 보유종목 {holding_count}/{max_holdings}")
				return
			
			# 잔고 확인
			balance_data = await get_balance(token=self.token)
			if isinstance(balance_data, dict):
				# 주문가능현금(ord_alowa)을 우선 사용, 없으면 d2_entra, 마지막으로 entr 사용
				# 우선순위: ord_alowa > mny_ord_able_amt > ord_alow_amt > d2_entra > entr
				balance = _to_int(balance_data.get('ord_alowa', 0))
				if balance <= 0:
					balance = _to_int(balance_data.get('mny_ord_able_amt', 0))
				if balance <= 0:
					balance = _to_int(balance_data.get('ord_alow_amt', 0))
				if balance <= 0:
					balance = _to_int(balance_data.get('d2_entra', 0))
				if balance <= 0:
					balance = _to_int(balance_data.get('entr', 0))
			else:
				balance = _to_int(balance_data)
			if balance <= 0:
				print("잔고 부족으로 돌파 매수를 건너뜁니다.")
				return
			
			# 매수 금액 계산
			buy_mode = cached_setting('buy_mode', 'ratio')
			if buy_mode == 'fixed_strict':
				expense = float(cached_setting('buy_fixed_amount', 100000.0))
				if balance < expense:
					print(f"돌파 매수 건너뜀: 잔고 부족 (설정: {int(expense):,}원, 잔고: {balance:,}원, bftx 모드)")
					return
			elif buy_mode == 'fixed':
				expense = float(cached_setting('buy_fixed_amount', 100000.0))
			else:
				buy_ratio = float(cached_setting('buy_ratio', 5.0)) / 100
				expense = balance * buy_ratio
			
			if buy_mode != 'fixed_strict' and expense > balance:
				expense = balance
			
			if expense <= 0:
				print("지출 금액이 0원 이하입니다.")
				return
			
			# 호가 조회
			bid = int(await check_bid(stock_code, token=self.token))
			if bid <= 0:
				print(f"호가 조회 실패 또는 0원({stock_code})")
				return
			
			order_qty = int(expense // bid)
			if order_qty <= 0:
				print("주문 수량이 0입니다.")
				return
			
			stock_data['status'] = 'ORDERING'
			
			# API 요청 간 짧은 대기
			await asyncio.sleep(0.3)
			
			# buy_stock은 이제 (return_code, order_no) 튜플을 반환
			buy_result, order_no = await buy_stock(stock_code, order_qty, bid, token=self.token)
			if buy_result != 0:
				stock_data['status'] = 'WATCH'
				print(f"돌파 매수 주문 실패: {stock_code}")
				return
			
			# 성공 시 Portfolio 업데이트
			stock_data['quantity'] = order_qty
			stock_data['avg_price'] = bid
			stock_data['high_price'] = current_price
			stock_data['status'] = 'HOLDING'
			
			name = stock_data.get('name', stock_code)
			message = f"🟢 {name} {order_qty}주 매수 주문 (돌파율: {increase_rate:.2f}%) [돌파 매수]"
			await tel_send(message)
			print(message)
		except Exception as e:
			print(f"돌파 매수 주문 중 오류({stock_code}): {e}")
			if stock_code in self.portfolio:
				self.portfolio[stock_code]['status'] = 'WATCH'
	
	async def _execute_sell_order(self, stock_code, stock_data, current_price, profit_rate, reason):
		"""매도 주문 실행"""
		already_processing = stock_code in self.processing_stocks
		# 매도 주문이 네트워크를 타고 가는 동안 들어오는 매수 신호를 차단하기 위해,
		# sell_stock 호출 '이전'부터 인메모리 락을 선점합니다.
		self.processing_stocks.add(stock_code)
		
		try:
			# 그리드 트레이딩 종목 체크 (가장 먼저 확인)
			if is_in_grid_trading(stock_code):
				print(f"{stock_data['name']} ({stock_code}): 그리드 트레이딩 중인 종목이므로 자동매도를 건너뜁니다.")
				return
			
			# 분할 트레이딩 종목 체크
			if is_in_wave_trading(stock_code):
				print(f"{stock_data['name']} ({stock_code}): 분할 트레이딩 중인 종목이므로 자동매도를 건너뜁니다.")
				return
			
			# 자동매도 금지 목록 체크
			if is_blocked(stock_code):
				print(f"{stock_data['name']} ({stock_code}): 자동매도 금지 목록에 있어 매도를 건너뜁니다.")
				await self._unregister_stock_quote(stock_code)
				return
			
			# status를 ORDERING으로 변경
			stock_data['status'] = 'ORDERING'
			
			# 매도 주문 전송
			quantity = stock_data['quantity']
			
			# API 요청 제한 방지: 0.5초 대기
			await asyncio.sleep(0.5)
			
			# 타입 안전성 확보: 수량을 문자열로 변환하여 전달
			sell_result, _ = await sell_stock(stock_code, str(quantity), token=self.token)
			
			if sell_result != 0:
				# 주문 실패 시 status 복구
				stock_data['status'] = 'HOLDING'
				print(f"매도 주문 실패: {stock_code}")
				return
			
			# 매도 성공 시 기록
			record_sold_stock(stock_code)
			
			# 텔레그램 알림
			result_emoji = "🔴" if reason == "익절" else "🔵" if reason == "손절" else "🟡"
			message = f'{result_emoji} {stock_data["name"]} ({stock_code}) {quantity}주 매도 주문 (수익율: {profit_rate:.2f}%) [{reason}]'
			await tel_send(message)
			print(message)
		
		except Exception as e:
			print(f"매도 주문 실행 중 오류: {e}")
			# 오류 시 status 복구
			if stock_code in self.portfolio:
				self.portfolio[stock_code]['status'] = 'HOLDING'
		finally:
			# 다른 흐름에서 이미 락을 잡고 있던 경우(드물지만), 그 락을 임의로 해제하지 않기 위해
			# 이 메서드가 "처음" 락을 잡은 경우에만 해제합니다.
			if not already_processing and stock_code in self.processing_stocks:
				self.processing_stocks.remove(stock_code)
	
	async def _handle_balance(self, response):
		"""04 (잔고) 메시지 처리: 잔고 동기화"""
		try:
			value = response.get('values', {})
			if not value:
				return
			
			stock_code = value.get('9001', '')
			if not stock_code:
				return
			
			# 보유수량 및 매입단가 파싱
			quantity_str = value.get('930', '0')
			avg_price_str = value.get('931', '0')
			
			try:
				quantity = int(quantity_str) if quantity_str else 0
				# 콤마 제거 후 변환
				avg_price_str_clean = str(avg_price_str).replace(',', '').strip() if avg_price_str else '0'
				avg_price = float(avg_price_str_clean) if avg_price_str_clean else 0.0
			except (ValueError, TypeError):
				return
			
			# 기존 종목인 경우
			if stock_code in self.portfolio:
				if quantity <= 0:
					# 전량 매도
					stock_data = self.portfolio[stock_code]
					stock_data['status'] = 'SOLD'
					del self.portfolio[stock_code]
					await self._unregister_stock_quote(stock_code)
					print(f"전량 매도 완료: {stock_code}")
				else:
					# 잔고 업데이트
					# 새로운 평균단가가 0 이하이고, 기존 portfolio에 유효한(0보다 큰) 평균단가가 있으면 덮어쓰지 않음
					existing_avg_price = self.portfolio[stock_code].get('avg_price', 0.0) or 0.0
					if avg_price <= 0 and existing_avg_price > 0:
						avg_price = existing_avg_price
						print(f"잔고 업데이트: {stock_code} - 평균단가가 0이므로 기존 유효값({avg_price:,.0f}원) 유지")
					
					self.portfolio[stock_code]['quantity'] = quantity
					self.portfolio[stock_code]['avg_price'] = avg_price
					self.portfolio[stock_code]['last_update_time'] = time.time()
					if self.portfolio[stock_code]['status'] == 'ORDERING':
						self.portfolio[stock_code]['status'] = 'HOLDING'
					print(f"잔고 업데이트: {stock_code} - 수량: {quantity}, 평단가: {avg_price:,.0f}")
			else:
				# 신규 종목 (매수 체결)
				if quantity > 0:
					# 종목명 조회 (비동기로 실행하여 블로킹 방지)
					try:
						stock_info_result = await stock_info(stock_code, token=self.token)
						stock_name = stock_info_result.get('stk_nm', stock_code) if isinstance(stock_info_result, dict) else stock_code
					except:
						stock_name = stock_code
					
					# Portfolio에 추가
					self.portfolio[stock_code] = {
						'name': stock_name,
						'avg_price': avg_price,
						'quantity': quantity,
						'high_price': avg_price,  # 초기값은 평단가, 첫 0B 수신 시 갱신
						'status': 'HOLDING',
						'last_update_time': time.time()
					}
					
					# 0B 등록 (디바운싱 적용)
					await self._queue_reg_request([stock_code], ['0B'], force_refresh=False)
					print(f"신규 종목 추가: {stock_name} ({stock_code}) - 수량: {quantity}, 평단가: {avg_price:,.0f}")
		
		except Exception as e:
			print(f"잔고 메시지 처리 중 오류: {e}")
	
	async def _handle_order_execution(self, response):
		"""00 (주문체결) 메시지 처리: 주문 상태 확인"""
		try:
			value = response.get('values', {})
			if not value:
				return
			
			order_status = value.get('913', '')  # 주문상태
			order_type = value.get('905', '')    # 주문구분
			stock_code = value.get('9001', '')   # 종목코드
			executed_price_str = value.get('901', '')  # 체결가격
			executed_qty_str = value.get('900', '')   # 체결수량
			order_no_raw = value.get('9203', '')  # 주문번호
			
			# 수동 매도 주문 체결 확인 및 이벤트 발생
			if order_status == '체결' and order_type == '매도' and order_no_raw:
				try:
					# 주문번호 정규화 (앞의 0 제거를 위해 정수 변환 후 문자열로)
					order_no = str(int(str(order_no_raw).strip()))
					
					# manual_sell_events에 등록된 주문인지 확인
					if order_no in manual_sell_events:
						event = manual_sell_events[order_no]
						if not event.is_set():
							event.set()
							print(f"수동 매도 주문 체결 확인: 주문번호 {order_no}, 종목코드 {stock_code}")
				except (ValueError, TypeError) as e:
					# 주문번호 파싱 실패 시 무시
					print(f"주문번호 파싱 실패: {order_no_raw}, 오류: {e}")
			
			# NOTE: 체결(00) 수신 시 텔레그램 체결 알림은 제거됨 (요청사항)
			
			# 기존 포트폴리오 관리 로직 (기능 2, 5)
			if not stock_code or stock_code not in self.portfolio:
				return
			
			# 매도 주문 체결 확인
			if order_status == '체결' and order_type == '매도':
				stock_data = self.portfolio[stock_code]
				stock_data['status'] = 'SOLD'
				# 잔고(04) 메시지로 최종 확인 후 portfolio에서 삭제
				print(f"매도 주문 체결 확인: {stock_code}")
			
			# 매수 주문 체결 확인
			elif order_status == '체결' and order_type == '매수':
				# 잔고(04) 메시지로 최종 확인 후 portfolio에 추가
				print(f"매수 주문 체결 확인: {stock_code}")
		
		except Exception as e:
			print(f"주문체결 메시지 처리 중 오류: {e}")
	
