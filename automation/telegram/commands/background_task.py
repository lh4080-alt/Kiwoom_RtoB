import asyncio
import datetime
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from trading.golden_cross_buy import check_and_buy_golden_cross
from trading.dead_cross_sell import check_and_sell_dead_cross
from telegram.tel_send import tel_send
from api.acc_val import fn_kt00004
from api.stk_rank import fn_ka00198
from utils.market_hour import MarketHour
from utils.stock_code_normalizer import normalize_stock_code

class BackgroundTaskManager:
	"""백그라운드 태스크 관리를 담당하는 클래스 - 선택된 기능만 실행"""
	
	def __init__(self, token_manager, settings_manager, websocket=None, process_command_callback=None):
		self.token_manager = token_manager
		self.settings_manager = settings_manager
		self.websocket = websocket  # 통합 웹소켓
		self.process_command_callback = process_command_callback
		
		# 활성화된 기능 번호들 (1~6)
		self.active_features = set()
		
		# 각 기능별 태스크
		self.task_1 = None  # 기능 1: 조건식 검색 매수 (websocket)
		self.task_3_4 = None  # 기능 3+4: 골든크로스/데드크로스 (차트 체크 루프)
		self.task_7 = None  # 기능 7: 그리드 트레이딩 (가격 체크 루프)
		self.task_8 = None  # 기능 8: 분할 트레이딩 (가격 체크 루프)
		self.task_bto = None  # bto: 매수 주문 타임아웃 처리 (10초마다 실행)
		# 기능 2, 5는 웹소켓 기반으로 처리 (별도 태스크 없음)
		
		# 기능 3, 4용 변수
		self.selected_stocks = []
		self.last_chart_check_time = None
		self.is_running = False
		
		# 기능 7용 변수
		self.grid_running = False
		
		# 기능 8용 변수
		self.wave_running = False
		
		# bto용 변수
		self.bto_running = False
	
	# ========== 기능 5: 트레일링 스탑 매도 ==========
	async def _start_feature_5(self):
		"""기능 5: 트레일링 스탑 매도 시작 (웹소켓 기반)"""
		# 웹소켓 연결 확인은 start_features에서 이미 수행됨
		if not self.websocket or not self.websocket.connected:
			return False
		
		try:
			success = await self.websocket.start_feature_5()
			if success:
				print("기능 5 (트레일링 스탑 매도) 시작됨")
				return True
			else:
				await tel_send("❌ 기능 5 시작 실패: Portfolio 초기화 실패")
				return False
		except Exception as e:
			print(f"기능 5 시작 중 오류: {e}")
			await tel_send(f"❌ 기능 5 시작 중 오류: {e}")
			return False
	
	async def _stop_feature_5(self):
		"""기능 5: 트레일링 스탑 매도 중지"""
		if self.websocket:
			try:
				await self.websocket.stop_feature_5()
				print("기능 5 (트레일링 스탑 매도) 중지됨")
			except Exception as e:
				print(f"기능 5 중지 중 오류: {e}")
	
	# ========== 기능 6: 돌파 매수 ==========
	async def _start_feature_6(self):
		"""기능 6: 돌파 매수 시작 (웹소켓 기반)"""
		if not self.websocket or not self.websocket.connected:
			return False
		
		try:
			success = await self.websocket.start_feature_6()
			if success:
				print("기능 6 (돌파 매수) 시작됨")
				return True
			else:
				await tel_send("❌ 기능 6 시작 실패: 감시 목록 초기화 실패")
				return False
		except Exception as e:
			print(f"기능 6 시작 중 오류: {e}")
			await tel_send(f"❌ 기능 6 시작 중 오류: {e}")
			return False
	
	async def _stop_feature_6(self):
		"""기능 6: 돌파 매수 중지"""
		if self.websocket:
			try:
				await self.websocket.stop_feature_6()
				print("기능 6 (돌파 매수) 중지됨")
			except Exception as e:
				print(f"기능 6 중지 중 오류: {e}")
	
	# ========== 기능 1: 조건식 검색 매수 ==========
	async def _start_feature_1(self, token):
		"""기능 1: 조건식 검색 매수 시작"""
		# 웹소켓 연결 확인은 start_features에서 이미 수행됨
		if not self.websocket or not self.websocket.connected:
			return False
		
		try:
			await self.websocket.start_feature_1()
			print("기능 1 (조건식 검색 매수) 시작됨")
			return True
		except Exception as e:
			print(f"기능 1 시작 중 오류: {e}")
			await tel_send(f"❌ 기능 1 시작 중 오류: {e}")
			return False
	
	async def _stop_feature_1(self):
		"""기능 1: 조건식 검색 매수 중지"""
		if self.websocket:
			try:
				await self.websocket.stop_feature_1()
				print("기능 1 (조건식 검색 매수) 중지됨")
			except Exception as e:
				print(f"기능 1 중지 중 오류: {e}")
	
	# ========== 기능 2: 수익율 매도 ==========
	async def _start_feature_2(self):
		"""기능 2: 수익율 매도 시작 (웹소켓 기반)"""
		# 웹소켓 연결 확인은 start_features에서 이미 수행됨
		if not self.websocket or not self.websocket.connected:
			return False
		
		try:
			success = await self.websocket.start_feature_2()
			if success:
				print("기능 2 (수익율 매도) 시작됨")
				return True
			else:
				await tel_send("❌ 기능 2 시작 실패: Portfolio 초기화 실패")
				return False
		except Exception as e:
			print(f"기능 2 시작 중 오류: {e}")
			await tel_send(f"❌ 기능 2 시작 중 오류: {e}")
			return False
	
	async def _stop_feature_2(self):
		"""기능 2: 수익율 매도 중지"""
		if self.websocket:
			try:
				await self.websocket.stop_feature_2()
				print("기능 2 (수익율 매도) 중지됨")
			except Exception as e:
				print(f"기능 2 중지 중 오류: {e}")
	
	# ========== 기능 3+4: 골든크로스/데드크로스 ==========
	async def _wait_for_market_start_plus_one_minute(self):
		"""장 시작 후 1분까지 대기"""
		now = datetime.datetime.now()
		market_start = now.replace(hour=MarketHour.MARKET_START_HOUR, minute=MarketHour.MARKET_START_MINUTE, second=0, microsecond=0)
		wait_until = market_start + datetime.timedelta(minutes=1)
		
		if now < wait_until:
			wait_seconds = (wait_until - now).total_seconds()
			print(f"장 시작 1분 후까지 {wait_seconds}초 대기...")
			await asyncio.sleep(wait_seconds)
	
	async def _check_charts_and_trade(self):
		"""차트를 확인하고 골든크로스/데드크로스에 따라 매수/매도"""
		max_retries = 5
		retry_delay = 1
		
		for attempt in range(max_retries):
			try:
				# 보유 종목 확인
				my_stocks = await fn_kt00004(False, 'N', '', self.token_manager.token)
				held_stock_codes = [normalize_stock_code(stock['stk_cd']) for stock in my_stocks] if my_stocks else []
				
				# 체크할 종목 리스트 결정
				# 기능 3(골든크로스 매수): 선정된 종목 + 보유 종목
				# 기능 4(데드크로스 매도): 보유 종목만
				if 3 in self.active_features:
					# 기능 3이 활성화된 경우: 선정된 종목과 보유 종목 모두 체크
					stocks_to_check = list(set(self.selected_stocks + held_stock_codes))
				else:
					# 기능 3이 비활성화된 경우: 보유 종목만 체크 (기능 4용)
					stocks_to_check = held_stock_codes
				
				# 모든 종목에 대해 차트 확인
				for stk_cd in stocks_to_check:
					# 기능 3: 골든크로스 매수
					if 3 in self.active_features:
						await check_and_buy_golden_cross(
							stk_cd, self.selected_stocks, held_stock_codes,
							self.settings_manager, self.token_manager.token
						)
					
					# 기능 4: 데드크로스 매도
					if 4 in self.active_features:
						await check_and_sell_dead_cross(
							stk_cd, held_stock_codes,
							self.settings_manager, self.token_manager.token
						)
				
				return
						
			except Exception as e:
				print(f"차트 체크 중 오류 (시도 {attempt + 1}/{max_retries}): {e}")
				if attempt < max_retries - 1:
					await asyncio.sleep(retry_delay)
				else:
					print(f"차트 체크 실패: 최대 재시도 횟수({max_retries}) 초과")
	
	async def _chart_check_loop(self):
		"""기능 3+4: 차트 체크 루프"""
		try:
			# 장 시작 1분 후 종목 선정 (기능 3이 활성화된 경우만)
			if 3 in self.active_features:
				await self._wait_for_market_start_plus_one_minute()
				
				# 종목 순위 조회 및 선정
				stock_count = self.settings_manager.get_setting('stock_count', 10)
				ranked_stocks = await fn_ka00198('N', '', self.token_manager.token)
				
				if not ranked_stocks:
					await tel_send("❌ 종목 순위 조회에 실패했습니다")
					return
				
				# 상위 stock_count개 종목 선정
				self.selected_stocks = [stock['stk_cd'] for stock in ranked_stocks[:stock_count]]
				await tel_send(f"✅ 상위 {stock_count}개 종목 선정 완료: {', '.join(self.selected_stocks)}")
			
			# 장 시작 시간 계산
			now = datetime.datetime.now()
			market_start = now.replace(hour=MarketHour.MARKET_START_HOUR, minute=MarketHour.MARKET_START_MINUTE, second=0, microsecond=0)
			start_time = market_start
			
			# 차트 체크 주기 설정에서 가져오기 (장기 분봉에 맞춤)
			chart_long = self.settings_manager.get_setting('chart_long', 20)
			check_interval_minutes = chart_long
			check_interval_seconds = check_interval_minutes * 60
			
			# 차트 체크 루프
			while self.is_running and (3 in self.active_features or 4 in self.active_features) and MarketHour.is_market_open_time():
				current_time = datetime.datetime.now()
				
				# 장 시작 후 설정된 주기마다 체크
				minutes_since_start = (current_time - start_time).total_seconds() / 60
				
				if minutes_since_start >= check_interval_minutes and (self.last_chart_check_time is None or 
					(current_time - self.last_chart_check_time).total_seconds() >= check_interval_seconds):
					
					await self._check_charts_and_trade()
					self.last_chart_check_time = current_time
				
				await asyncio.sleep(60)  # 1분마다 체크
				
		except asyncio.CancelledError:
			print("기능 3+4 (골든크로스/데드크로스) 루프가 중지되었습니다")
		except Exception as e:
			print(f"기능 3+4 루프 오류: {e}")
			await tel_send(f"❌ 차트 체크 루프 오류: {e}")
	
	async def _start_feature_3_4(self, token):
		"""기능 3+4: 골든크로스/데드크로스 시작"""
		self.is_running = True
		self.selected_stocks = []
		self.last_chart_check_time = None
		
		if self.task_3_4 and not self.task_3_4.done():
			print("기존 기능 3+4 태스크를 정지합니다")
			self.task_3_4.cancel()
			try:
				await self.task_3_4
			except asyncio.CancelledError:
				pass
		
		self.task_3_4 = asyncio.create_task(self._chart_check_loop())
		
		features_str = []
		if 3 in self.active_features:
			features_str.append("골든크로스 매수")
		if 4 in self.active_features:
			features_str.append("데드크로스 매도")
		print(f"기능 3+4 ({', '.join(features_str)}) 시작됨")
	
	async def _stop_feature_3_4(self):
		"""기능 3+4: 골든크로스/데드크로스 중지"""
		self.is_running = False
		
		if self.task_3_4 and not self.task_3_4.done():
			print("기능 3+4 (골든크로스/데드크로스) 백그라운드 태스크를 정지합니다")
			self.task_3_4.cancel()
			try:
				await self.task_3_4
			except asyncio.CancelledError:
				pass
		
		self.selected_stocks = []
		self.last_chart_check_time = None
	
	# ========== 기능 7: 그리드 트레이딩 ==========
	async def _grid_trading_loop(self):
		"""기능 7: 그리드 트레이딩 루프"""
		from trading.grid_manager import check_and_trade, load_grid_status
		
		try:
			script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
			
			# 루프 시작 전에 토큰을 한 번만 가져오기 (없을 때만 새로 발급)
			if not self.token_manager.token:
				token = await self.token_manager.get_token()
				if not token:
					print("그리드 트레이딩: 토큰 발급 실패")
					return
			
			while self.grid_running and 7 in self.active_features and MarketHour.is_market_open_time():
				try:
					# grid_status.json 로드
					grid_data = load_grid_status(script_dir)
					
					if not grid_data:
						# 등록된 종목이 없으면 1초 대기 후 다시 확인
						await asyncio.sleep(1)
						continue
					
					# 토큰 재사용 (다른 기능들과 동일하게)
					token = self.token_manager.token
					if not token:
						# 토큰이 없으면 한 번만 새로 발급 시도
						token = await self.token_manager.get_token()
						if not token:
							print("그리드 트레이딩: 토큰 발급 실패")
							await asyncio.sleep(1)
							continue
					
					# 모든 등록된 종목에 대해 체크
					for stock_code in list(grid_data.keys()):
						if not self.grid_running or 7 not in self.active_features:
							break
						
						await check_and_trade(script_dir, stock_code, token)
						
						# API 호출 제한을 고려하여 약간의 대기
						await asyncio.sleep(0.5)
					
					# 1초 대기 (API 호출 제한 고려)
					await asyncio.sleep(1)
					
				except Exception as e:
					print(f"그리드 트레이딩 루프 오류: {e}")
					await asyncio.sleep(1)
					
		except asyncio.CancelledError:
			print("기능 7 (그리드 트레이딩) 루프가 중지되었습니다")
		except Exception as e:
			print(f"기능 7 루프 오류: {e}")
			await tel_send(f"❌ 그리드 트레이딩 루프 오류: {e}")
	
	async def _start_feature_7(self, token):
		"""기능 7: 그리드 트레이딩 시작"""
		self.grid_running = True
		
		if self.task_7 and not self.task_7.done():
			print("기존 기능 7 태스크를 정지합니다")
			self.task_7.cancel()
			try:
				await self.task_7
			except asyncio.CancelledError:
				pass
		
		self.task_7 = asyncio.create_task(self._grid_trading_loop())
		print("기능 7 (그리드 트레이딩) 시작됨")
	
	async def _stop_feature_7(self):
		"""기능 7: 그리드 트레이딩 중지"""
		self.grid_running = False
		
		if self.task_7 and not self.task_7.done():
			print("기능 7 (그리드 트레이딩) 백그라운드 태스크를 정지합니다")
			self.task_7.cancel()
			try:
				await self.task_7
			except asyncio.CancelledError:
				pass
		
		self.task_7 = None
	
	# ========== 기능 8: 분할 트레이딩 ==========
	async def _wave_trading_loop(self):
		"""기능 8: 분할 트레이딩 루프"""
		from trading.wave_manager import WaveManager
		
		try:
			script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
			wave_manager = WaveManager()
			
			# 루프 시작 전에 토큰을 한 번만 가져오기 (없을 때만 새로 발급)
			if not self.token_manager.token:
				token = await self.token_manager.get_token()
				if not token:
					print("분할 트레이딩: 토큰 발급 실패")
					return
			
			while self.wave_running and 8 in self.active_features and MarketHour.is_market_open_time():
				try:
					# 토큰 재사용 (다른 기능들과 동일하게)
					token = self.token_manager.token
					if not token:
						# 토큰이 없으면 한 번만 새로 발급 시도
						token = await self.token_manager.get_token()
						if not token:
							print("분할 트레이딩: 토큰 발급 실패")
							await asyncio.sleep(1)
							continue
					
					# 분할 트레이딩 감시 및 실행
					await wave_manager.check_and_execute(token)
					
					# 1초 대기 (API 호출 제한 고려)
					await asyncio.sleep(1)
					
				except Exception as e:
					print(f"분할 트레이딩 루프 오류: {e}")
					await asyncio.sleep(1)
					
		except asyncio.CancelledError:
			print("기능 8 (분할 트레이딩) 루프가 중지되었습니다")
		except Exception as e:
			print(f"기능 8 루프 오류: {e}")
			await tel_send(f"❌ 분할 트레이딩 루프 오류: {e}")
	
	async def _start_feature_8(self, token):
		"""기능 8: 분할 트레이딩 시작"""
		self.wave_running = True
		
		if self.task_8 and not self.task_8.done():
			print("기존 기능 8 태스크를 정지합니다")
			self.task_8.cancel()
			try:
				await self.task_8
			except asyncio.CancelledError:
				pass
		
		self.task_8 = asyncio.create_task(self._wave_trading_loop())
		print("기능 8 (분할 트레이딩) 시작됨")
	
	async def _stop_feature_8(self):
		"""기능 8: 분할 트레이딩 중지"""
		self.wave_running = False
		
		if self.task_8 and not self.task_8.done():
			print("기능 8 (분할 트레이딩) 백그라운드 태스크를 정지합니다")
			self.task_8.cancel()
			try:
				await self.task_8
			except asyncio.CancelledError:
				pass
		
		self.task_8 = None
	
	# ========== bto: 매수 주문 타임아웃 처리 ==========
	async def _bto_loop(self):
		"""bto 루프: 10초마다 미체결 매수주문을 확인하고 처리"""
		from api.check_unfilled import fn_ka10075
		from api.cancel_order import fn_sc10002
		from api.modify_order import fn_sc10001
		from utils.buy_order_tracker import get_tracker
		from utils.get_setting import get_setting
		from api.stock_info import fn_ka10001 as stock_info
		
		try:
			while self.bto_running and MarketHour.is_market_open_time():
				try:
					# bto 설정 확인
					buy_timeout = get_setting('buy_timeout', 0)
					
					# bto가 비활성화되어 있으면 대기만
					if buy_timeout <= 0:
						await asyncio.sleep(10)
						continue
					
					buy_timeout_action = get_setting('buy_timeout_action', 'cancel')
					
					# 토큰 확인
					token = self.token_manager.token
					if not token:
						token = await self.token_manager.get_token()
						if not token:
							await asyncio.sleep(10)
							continue
					
					# 미체결 매수주문 조회 (trde_tp='2'는 매수)
					unfilled_orders = await fn_ka10075(
						stk_cd='',
						trde_tp='2',  # 매수만
						stex_tp='0',  # 통합 거래소
						token=token
					)
					
					if not unfilled_orders or len(unfilled_orders) == 0:
						await asyncio.sleep(10)
						continue
					
					# 주문 추적기 가져오기
					tracker = get_tracker()
					
					# 현재 시간
					now = datetime.datetime.now()
					
					# 각 미체결 주문 처리
					for order in unfilled_orders:
						if not isinstance(order, dict):
							continue
						
						# 주문 정보 추출
						ord_no = order.get('ord_no') or order.get('orgn_ord_no') or order.get('odno')
						stk_cd = order.get('stk_cd', '')
						oso_qty_raw = order.get('oso_qty') or order.get('rmn_qty') or order.get('not_qty') or order.get('ord_qty', 0)
						
						try:
							ord_qty = int(oso_qty_raw) if oso_qty_raw else 0
						except (ValueError, TypeError):
							ord_qty = 0
						
						if not ord_no or not stk_cd or ord_qty <= 0:
							continue
						
						# 종목코드 정리
						stk_cd_clean = str(stk_cd).strip()
						if stk_cd_clean.startswith('A'):
							stk_cd_clean = stk_cd_clean[1:]
						
						# 주문번호 정리
						ord_no_str = str(ord_no).strip()
						
						# 주문이 추적 중인지 확인
						order_info = tracker.get_order(ord_no_str)
						
						if order_info:
							# 추적 중인 주문: bto 시간이 지났는지 확인
							order_time = order_info.get('order_time')
							if isinstance(order_time, datetime.datetime):
								elapsed_seconds = (now - order_time).total_seconds()
								
								# bto 시간이 지나지 않았으면 패스
								if elapsed_seconds < buy_timeout:
									continue
						
						# 추적되지 않은 주문이거나 bto 시간이 지난 주문: 취소 또는 시장가 전환
						try:
							# 종목명 조회
							info = await stock_info(stk_cd_clean, token=token)
							stock_name = info.get('stk_nm', stk_cd_clean) if isinstance(info, dict) else stk_cd_clean
						except Exception:
							stock_name = stk_cd_clean
						
						# 거래소 구분 추출
						dmst_stex_tp = order.get('dmst_stex_tp') or order.get('stex_tp_nm') or 'KRX'
						if isinstance(dmst_stex_tp, str) and dmst_stex_tp.isdigit():
							if dmst_stex_tp == '1':
								dmst_stex_tp = 'KRX'
							elif dmst_stex_tp == '2':
								dmst_stex_tp = 'NXT'
							else:
								dmst_stex_tp = 'KRX'
						
						# 취소 또는 시장가 전환
						if buy_timeout_action == 'cancel':
							# 취소
							return_code = await fn_sc10002(
								stk_cd=stk_cd_clean,
								orgn_ord_no=ord_no_str,
								ord_qty=str(ord_qty),
								dmst_stex_tp=dmst_stex_tp,
								token=token
							)
							
							if return_code == 0:
								print(f"✅ [BTO] 미체결 매수주문 취소: {stock_name} ({stk_cd_clean}) 주문번호 {ord_no_str} ({ord_qty}주)")
								# 주문 기록 제거
								tracker.remove_order(ord_no_str)
							else:
								print(f"❌ [BTO] 미체결 매수주문 취소 실패: {stock_name} ({stk_cd_clean}) 주문번호 {ord_no_str} (오류 코드: {return_code})")
						
						elif buy_timeout_action == 'market':
							# 시장가로 정정
							return_code = await fn_sc10001(
								stk_cd=stk_cd_clean,
								orgn_ord_no=ord_no_str,
								ord_qty=str(ord_qty),
								token=token
							)
							
							if return_code == 0:
								print(f"✅ [BTO] 미체결 매수주문 시장가 전환: {stock_name} ({stk_cd_clean}) 주문번호 {ord_no_str} ({ord_qty}주)")
								# 주문 기록 제거
								tracker.remove_order(ord_no_str)
							else:
								print(f"❌ [BTO] 미체결 매수주문 시장가 전환 실패: {stock_name} ({stk_cd_clean}) 주문번호 {ord_no_str} (오류 코드: {return_code})")
						
						# Rate Limit 방지를 위한 지연
						await asyncio.sleep(1)
					
					# 오래된 주문 기록 정리 (24시간 이상 된 기록)
					tracker.cleanup_old_orders(max_age_hours=24)
					
				except Exception as e:
					print(f"bto 루프 오류: {e}")
				
				# 10초 대기
				await asyncio.sleep(10)
				
		except asyncio.CancelledError:
			print("bto 루프가 중지되었습니다")
		except Exception as e:
			print(f"bto 루프 오류: {e}")
			await tel_send(f"❌ bto 루프 오류: {e}")
	
	async def _start_bto(self):
		"""bto 시작"""
		# bto 설정 확인
		from utils.get_setting import get_setting
		buy_timeout = get_setting('buy_timeout', 0)
		
		# bto가 비활성화되어 있으면 시작하지 않음
		if buy_timeout <= 0:
			return
		
		self.bto_running = True
		
		if self.task_bto and not self.task_bto.done():
			print("기존 bto 태스크를 정지합니다")
			self.task_bto.cancel()
			try:
				await self.task_bto
			except asyncio.CancelledError:
				pass
		
		self.task_bto = asyncio.create_task(self._bto_loop())
		print("bto (매수 주문 타임아웃 처리) 시작됨")
	
	async def _stop_bto(self):
		"""bto 중지"""
		self.bto_running = False
		
		if self.task_bto and not self.task_bto.done():
			print("bto 백그라운드 태스크를 정지합니다")
			self.task_bto.cancel()
			try:
				await self.task_bto
			except asyncio.CancelledError:
				pass
		
		self.task_bto = None
	
	# ========== 통합 관리 메서드 ==========
	async def start_features(self, feature_numbers, token):
		"""
		선택된 기능들만 시작합니다.
		
		Args:
			feature_numbers: 기능 번호 리스트 (예: [1, 4] 또는 [2, 3, 4])
			token: API 토큰
		"""
		# 유효한 기능 번호만 필터링
		valid_features = [f for f in feature_numbers if f in [1, 2, 3, 4, 5, 6, 7, 8]]
		
		if not valid_features:
			await tel_send("❌ 유효한 기능 번호가 없습니다. 1, 2, 3, 4, 5, 6, 7, 8 중에서 선택해주세요.")
			return False
		
		# 웹소켓이 필요한 기능(1, 2, 5, 6)이 있는지 확인
		websocket_required_features = [f for f in valid_features if f in [1, 2, 5, 6]]
		if websocket_required_features:
			# 웹소켓 연결 확인 (한 번만)
			if not self.websocket:
				await tel_send("❌ 웹소켓이 초기화되지 않았습니다")
				return False
			
			if not self.websocket.connected:
				# 연결이 끊겨 있으면 재연결 시도
				print("웹소켓 연결이 끊겨 있습니다. 재연결을 시도합니다...")
				try:
					await self.websocket.connect(token)
					# 재연결 후 연결 상태 확인
					if not self.websocket.connected:
						raise Exception("재연결 실패")
					print("웹소켓 재연결 성공")
				except Exception as e:
					print(f"웹소켓 재연결 실패: {e}")
					# 모의투자 여부 확인
					is_paper = self.settings_manager.get_setting('is_paper_trading', True)
					trading_type = "[모의]" if is_paper else "[실거래]"
					await tel_send(f"{trading_type} ❌ 웹소켓이 연결되어 있지 않습니다 (재연결 실패)")
					return False
		
		# 요청된 기능들만 시작하고 결과 추적
		started_features = []
		failed_features = []
		skipped_features = []  # 이미 활성화된 기능 추적
		
		if 1 in valid_features:
			# 이미 활성화된 기능은 재시작하지 않음 (중복 실행 방지)
			if 1 in self.active_features:
				print("기능 1이 이미 활성화되어 있어 재시작을 건너뜁니다.")
				skipped_features.append(1)
			else:
				if await self._start_feature_1(token):
					started_features.append(1)
					self.active_features.add(1)
				else:
					failed_features.append(1)
		
		if 2 in valid_features:
			# 이미 활성화된 기능은 재시작하지 않음 (중복 실행 방지)
			if 2 in self.active_features:
				print("기능 2가 이미 활성화되어 있어 재시작을 건너뜁니다.")
				skipped_features.append(2)
			else:
				if await self._start_feature_2():
					started_features.append(2)
					self.active_features.add(2)
				else:
					failed_features.append(2)
		
		if 3 in valid_features or 4 in valid_features:
			# 기능 3 또는 4가 이미 활성화되어 있으면 재시작하지 않음
			if (3 in self.active_features or 4 in self.active_features) and \
			   (self.is_running and self.task_3_4 and not self.task_3_4.done()):
				print("기능 3 또는 4가 이미 실행 중이어서 재시작을 건너뜁니다.")
				if 3 in valid_features and 3 not in self.active_features:
					self.active_features.add(3)
					skipped_features.append(3)
				if 4 in valid_features and 4 not in self.active_features:
					self.active_features.add(4)
					skipped_features.append(4)
			else:
				await self._start_feature_3_4(token)
				if 3 in valid_features:
					started_features.append(3)
					self.active_features.add(3)
				if 4 in valid_features:
					started_features.append(4)
					self.active_features.add(4)
		
		if 5 in valid_features:
			# 이미 활성화된 기능은 재시작하지 않음 (중복 실행 방지)
			if 5 in self.active_features:
				print("기능 5가 이미 활성화되어 있어 재시작을 건너뜁니다.")
				skipped_features.append(5)
			else:
				if await self._start_feature_5():
					started_features.append(5)
					self.active_features.add(5)
				else:
					failed_features.append(5)
		
		if 6 in valid_features:
			# 이미 활성화된 기능은 재시작하지 않음 (중복 실행 방지)
			if 6 in self.active_features:
				print("기능 6이 이미 활성화되어 있어 재시작을 건너뜁니다.")
				skipped_features.append(6)
			else:
				if await self._start_feature_6():
					started_features.append(6)
					self.active_features.add(6)
				else:
					failed_features.append(6)
		
		if 7 in valid_features:
			# 이미 활성화된 기능은 재시작하지 않음 (중복 실행 방지)
			if 7 in self.active_features:
				print("기능 7이 이미 활성화되어 있어 재시작을 건너뜁니다.")
				skipped_features.append(7)
			else:
				await self._start_feature_7(token)
				started_features.append(7)
				self.active_features.add(7)
		
		if 8 in valid_features:
			# 이미 활성화된 기능은 재시작하지 않음 (중복 실행 방지)
			if 8 in self.active_features:
				print("기능 8이 이미 활성화되어 있어 재시작을 건너뜁니다.")
				skipped_features.append(8)
			else:
				await self._start_feature_8(token)
				started_features.append(8)
				self.active_features.add(8)
		
		# 시작된 기능이 없고, 건너뛴 기능도 없으면 실패
		if not started_features and not skipped_features:
			if failed_features:
				await tel_send("❌ 모든 기능 시작에 실패했습니다")
			return False
		
		# 성공 메시지 구성
		features_desc = []
		if 1 in started_features:
			features_desc.append("1:조건식 검색 매수")
		if 2 in started_features:
			features_desc.append("2:수익율 매도")
		if 3 in started_features:
			features_desc.append("3:골든크로스 매수")
		if 4 in started_features:
			features_desc.append("4:데드크로스 매도")
		if 5 in started_features:
			features_desc.append("5:트레일링 스탑 매도")
		if 6 in started_features:
			features_desc.append("6:돌파 매수")
		if 7 in started_features:
			features_desc.append("7:그리드 트레이딩")
		if 8 in started_features:
			features_desc.append("8:분할 트레이딩")
		
		success_msg = ""
		if started_features:
			success_msg = f"✅ 다음 기능들이 시작되었습니다: {', '.join(features_desc)}"
		
		# 건너뛴 기능이 있으면 메시지에 추가
		if skipped_features:
			skipped_desc = []
			if 1 in skipped_features:
				skipped_desc.append("1:조건식 검색 매수")
			if 2 in skipped_features:
				skipped_desc.append("2:수익율 매도")
			if 3 in skipped_features:
				skipped_desc.append("3:골든크로스 매수")
			if 4 in skipped_features:
				skipped_desc.append("4:데드크로스 매도")
			if 5 in skipped_features:
				skipped_desc.append("5:트레일링 스탑 매도")
			if 6 in skipped_features:
				skipped_desc.append("6:돌파 매수")
			if 7 in skipped_features:
				skipped_desc.append("7:그리드 트레이딩")
			if 8 in skipped_features:
				skipped_desc.append("8:분할 트레이딩")
			if success_msg:
				success_msg += f"\n⚠️ 다음 기능들은 이미 실행 중이어서 건너뛰었습니다: {', '.join(skipped_desc)}"
			else:
				success_msg = f"⚠️ 다음 기능들은 이미 실행 중이어서 건너뛰었습니다: {', '.join(skipped_desc)}"
		
		if failed_features:
			failed_desc = []
			if 1 in failed_features:
				failed_desc.append("1:조건식 검색 매수")
			if 2 in failed_features:
				failed_desc.append("2:수익율 매도")
			if 5 in failed_features:
				failed_desc.append("5:트레일링 스탑 매도")
			if 6 in failed_features:
				failed_desc.append("6:돌파 매수")
			if 7 in failed_features:
				failed_desc.append("7:그리드 트레이딩")
			if 8 in failed_features:
				failed_desc.append("8:분할 트레이딩")
			if success_msg:
				success_msg += f"\n❌ 다음 기능들은 시작에 실패했습니다: {', '.join(failed_desc)}"
			else:
				success_msg = f"❌ 다음 기능들은 시작에 실패했습니다: {', '.join(failed_desc)}"
		
		if success_msg:
			await tel_send(success_msg)
		
		# bto 시작 (매수를 하는 기능이 실행 중일 때만 시작)
		# 매수를 하는 기능: 1(조건식 검색 매수), 3(골든크로스 매수), 6(돌파 매수)
		buy_features = [1, 3, 6]
		has_buy_feature = any(f in self.active_features for f in buy_features)
		if has_buy_feature:
			await self._start_bto()
		
		return len(failed_features) == 0
	
	async def stop_features(self, feature_numbers):
		"""
		선택된 기능들만 중지합니다.
		
		Args:
			feature_numbers: 기능 번호 리스트 (예: [1, 4] 또는 [2, 3, 4])
		"""
		# 유효한 기능 번호만 필터링
		valid_features = [f for f in feature_numbers if f in [1, 2, 3, 4, 5, 6, 7, 8]]
		
		if not valid_features:
			await tel_send("❌ 유효한 기능 번호가 없습니다. 1, 2, 3, 4, 5, 6, 7, 8 중에서 선택해주세요.")
			return False
		
		# 재로그인 중이면 알림 없이 조용히 처리
		is_silent = self.websocket and self.websocket.is_relogging
		
		# 각 기능 중지
		if 1 in valid_features and 1 in self.active_features:
			await self._stop_feature_1()
			self.active_features.discard(1)
		
		if 2 in valid_features and 2 in self.active_features:
			await self._stop_feature_2()
			self.active_features.discard(2)
		
		if (3 in valid_features or 4 in valid_features) and (3 in self.active_features or 4 in self.active_features):
			await self._stop_feature_3_4()
			self.active_features.discard(3)
			self.active_features.discard(4)
		
		if 5 in valid_features and 5 in self.active_features:
			await self._stop_feature_5()
			self.active_features.discard(5)
		
		if 6 in valid_features and 6 in self.active_features:
			await self._stop_feature_6()
			self.active_features.discard(6)
		
		if 7 in valid_features and 7 in self.active_features:
			await self._stop_feature_7()
			self.active_features.discard(7)
		
		if 8 in valid_features and 8 in self.active_features:
			await self._stop_feature_8()
			self.active_features.discard(8)
		
		# 재로그인 중이 아니고 장 시간일 때만 알림 발송
		if not is_silent and MarketHour.is_market_open_time():
			features_desc = []
			if 1 in valid_features:
				features_desc.append("1:조건식 검색 매수")
			if 2 in valid_features:
				features_desc.append("2:수익율 매도")
			if 3 in valid_features:
				features_desc.append("3:골든크로스 매수")
			if 4 in valid_features:
				features_desc.append("4:데드크로스 매도")
			if 5 in valid_features:
				features_desc.append("5:트레일링 스탑 매도")
			if 6 in valid_features:
				features_desc.append("6:돌파 매수")
			if 7 in valid_features:
				features_desc.append("7:그리드 트레이딩")
			if 8 in valid_features:
				features_desc.append("8:분할 트레이딩")
			
			await tel_send(f"⏹️ 다음 기능들이 중지되었습니다: {', '.join(features_desc)}")
		
		# 매수를 하는 기능이 모두 중지되었으면 bto도 중지
		# 매수를 하는 기능: 1(조건식 검색 매수), 3(골든크로스 매수), 6(돌파 매수)
		buy_features = [1, 3, 6]
		has_buy_feature = any(f in self.active_features for f in buy_features)
		if not has_buy_feature:
			await self._stop_bto()
		
		return True
	
	async def stop_all(self):
		"""모든 활성화된 기능을 중지합니다."""
		active_list = list(self.active_features)
		if active_list:
			await self.stop_features(active_list)
		else:
			await tel_send("⚠️ 실행 중인 기능이 없습니다")
			# 기능이 없어도 bto는 중지
			await self._stop_bto()
	
	@property
	def is_running_any(self):
		"""실행 중인 기능이 있는지 확인"""
		return len(self.active_features) > 0
	
	async def on_relogin_complete(self, new_token, active_features_before_relogin):
		"""
		재로그인 완료 후 호출되는 콜백 함수
		
		Args:
			new_token: 새로 발급받은 토큰
			active_features_before_relogin: 재로그인 전 활성 기능 정보 딕셔너리
		"""
		try:
			# 토큰 동기화
			self.token_manager.token = new_token
			print(f"✅ 토큰이 새 토큰으로 갱신되었습니다.")
			
			# 재로그인 전 활성 기능 확인 및 재가동
			features_to_restart = []
			
			# 기능 1, 2, 5: 웹소켓 연결 및 데이터 수신 상태 확인
			if active_features_before_relogin.get('feature_1', False):
				if not self.websocket.feature_1_active:
					features_to_restart.append(1)
					print("기능 1이 재로그인 전에 활성화되어 있었지만 현재 비활성화 상태입니다. 재가동합니다.")
			
			if active_features_before_relogin.get('feature_2', False):
				if not self.websocket.feature_2_active:
					features_to_restart.append(2)
					print("기능 2가 재로그인 전에 활성화되어 있었지만 현재 비활성화 상태입니다. 재가동합니다.")
			
			if active_features_before_relogin.get('feature_5', False):
				if not self.websocket.feature_5_active:
					features_to_restart.append(5)
					print("기능 5가 재로그인 전에 활성화되어 있었지만 현재 비활성화 상태입니다. 재가동합니다.")
			
			if active_features_before_relogin.get('feature_6', False):
				if not self.websocket.feature_6_active:
					features_to_restart.append(6)
					print("기능 6이 재로그인 전에 활성화되어 있었지만 현재 비활성화 상태입니다. 재가동합니다.")
			
			# 기능 3, 4: 차트 체크 루프가 실행 중인지 확인
			if 3 in self.active_features or 4 in self.active_features:
				if not self.is_running or self.task_3_4 is None or self.task_3_4.done():
					# 차트 체크 루프가 중지된 경우 재시작
					if 3 in self.active_features or 4 in self.active_features:
						features_to_restart.extend([f for f in [3, 4] if f in self.active_features])
						print("기능 3 또는 4의 차트 체크 루프가 중지된 상태입니다. 재가동합니다.")
			
			# 기능 7: 그리드 트레이딩 루프가 실행 중인지 확인
			if 7 in self.active_features:
				if not self.grid_running or self.task_7 is None or self.task_7.done():
					features_to_restart.append(7)
					print("기능 7의 그리드 트레이딩 루프가 중지된 상태입니다. 재가동합니다.")
			
			# 기능 8: 분할 트레이딩 루프가 실행 중인지 확인
			if 8 in self.active_features:
				if not self.wave_running or self.task_8 is None or self.task_8.done():
					features_to_restart.append(8)
					print("기능 8의 분할 트레이딩 루프가 중지된 상태입니다. 재가동합니다.")
			
			# 재가동이 필요한 기능이 있으면 재시작
			if features_to_restart:
				print(f"재가동할 기능: {features_to_restart}")
				await self.start_features(features_to_restart, new_token)
				await tel_send(f"✅ 재로그인 완료 후 다음 기능들이 자동으로 재가동되었습니다: {', '.join(map(str, features_to_restart))}")
			else:
				print("✅ 재로그인 완료: 모든 기능이 정상적으로 동작 중입니다.")
			
			# bto 재시작 (매수를 하는 기능이 실행 중이면 bto도 재시작)
			# 매수를 하는 기능: 1(조건식 검색 매수), 3(골든크로스 매수), 6(돌파 매수)
			buy_features = [1, 3, 6]
			has_buy_feature = any(f in self.active_features for f in buy_features)
			if has_buy_feature:
				await self._start_bto()
				
		except Exception as e:
			print(f"재로그인 완료 콜백 실행 중 오류: {e}")
			await tel_send(f"❌ 재로그인 완료 후 기능 재가동 중 오류가 발생했습니다: {e}")

