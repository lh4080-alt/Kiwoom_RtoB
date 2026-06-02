import os
import asyncio
import sys

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realtime.websocket import UnifiedWebSocket
from telegram.tel_send import tel_send
from telegram.commands.settings import SettingsManager
from telegram.commands.token_manager import TokenManager
from telegram.commands.background_task import BackgroundTaskManager
from utils.market_hour import MarketHour
from telegram.commands.start_command import start_command
from telegram.commands.stop_command import stop_command
from telegram.commands.report_command import report_command
from telegram.commands.condition_command import condition_command
from telegram.commands.top_command import top_command
from telegram.commands.chart_command import chart_command
from telegram.commands.setting_commands import (
	tpr_command,
	slr_command,
	gapup_command,
	gapdown_command,
	touch_rate_command,
	brt_command,
	bft_command,
	bftx_command,
	market_command,
	btp_command,
	bto_command,
	cooldown_command,
	tsr_command,
	maxholdings_command,
	block_add_command,
	block_remove_command,
	block_list_command,
	brk_rate_command,
	brk_add_command,
	brk_remove_command,
	brk_list_command,
)
from telegram.commands.help_command import help_command
from telegram.commands.sell_command import sell_command
from telegram.commands.sell_all_command import sell_all_command
from telegram.commands.cancel_unfilled_command import cancel_unfilled_command
from telegram.commands.grid_command import process_grid_add, process_grid_remove, process_grid_list
from telegram.commands.reserve_command import reserve_command
from telegram.commands.wave_command import (
	process_wave_set,
	process_wave_add,
	process_wave_list,
	process_wave_remove
)
from telegram.commands.search_command import search_command
from telegram.commands.buy_command import buy_command
from telegram.commands.rank_command import rank_command
from telegram.commands.fcond_command import fcond_command

class ChatCommand:
	def __init__(self):
		self.script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		
		# 관리자 클래스들 초기화
		self.settings_manager = SettingsManager(self.script_dir)
		self.token_manager = TokenManager()
		
		# 통합 웹소켓 초기화 (연결은 main.py에서 시작)
		# process_command_callback은 나중에 설정됨 (순환 참조 방지)
		self.websocket = UnifiedWebSocket(
			self.token_manager,
			on_connection_closed=self._on_connection_closed,
			on_relogin_complete=self._on_relogin_complete,
			process_command_callback=None  # 나중에 설정
		)
		
		self.background_task_manager = BackgroundTaskManager(
			self.token_manager,
			self.settings_manager,
			self.websocket,
			None  # process_command_callback는 나중에 설정
		)
		
		# 연결 끊김 시 재연결 태스크(중복 실행 방지)
		self._reconnect_task = None
		
		# 예약 스케줄러 초기화 (process_command_callback은 나중에 설정됨)
		from telegram.commands.reserve_command import get_scheduler
		# 스케줄러는 나중에 process_command에서 process_command_callback이 설정될 때 함께 업데이트됨
		self._reserve_scheduler = None

		# Daily task (매일 16:00 daily_analyzer + collection_pool 비우기)
		# 영구 원칙: 외부 프로세스 데이터 조작 금지 — 봇 내부에서만 처리.
		from core.daily_task import DailyTaskManager
		self.daily_task = DailyTaskManager(self)

		# Phase 2 Step B: 매수 후보 큐 영속화 (config/data/buy_queue.json)
		# pick/cancel/halt/resume 텔레그램 명령으로 조작. 09:00 매수 실행은 Step C.
		# buy_queue는 매번 utils/buy_queue.py 함수로 디스크에서 read/write — 메모리 캐시 X.
		self.is_halted: bool = False

		# Phase 2 Step B: daily_analyzer — 16:00 자동 분석 + 텔레그램 알림
		from modules.daily_analyzer import DailyAnalyzer
		self.daily_analyzer = DailyAnalyzer(bot_ref=self)

		# Phase 2 Step C: buy_executor (09:00 매수) + holdings_manager (0B 손절/시한/한도)
		from modules.buy_executor import BuyExecutor
		from modules.holdings_manager import HoldingsManager
		self.buy_executor = BuyExecutor(bot_ref=self)
		self.holdings_manager = HoldingsManager(bot_ref=self)
		# websocket의 0B 핸들러가 보유 종목 가격 변동 시 holdings_manager.on_0b_quote 호출
		self.websocket.holdings_manager = self.holdings_manager

		# 차단 종목 재진입 감시 매수 (09:05~14:30 5분 polling)
		from modules.watching_buyer import WatchingBuyer
		self.watching_buyer = WatchingBuyer(bot_ref=self)

		# stick — 매일 08:30 SOX/NQ 체크 후 자동 매수 + 15:20 동시호가 매도
		from modules.stick_executor import StickExecutor
		self.stick_executor = StickExecutor(bot_ref=self)

		# touch — 0B 푸시 + 5분 fallback polling, 최저점 반등 시장가 매수
		from modules.touch_executor import TouchExecutor
		self.touch_executor = TouchExecutor(bot_ref=self)
		# websocket의 0B 핸들러가 touch_executor.on_0b_quote 호출하도록 연결
		self.websocket.touch_executor = self.touch_executor

		# semi_trigger snapshot 스케줄러 (02:00 + 05:30 KST 자동 + 텔레그램 score 명령)
		from modules.semi_trigger.scheduler import SnapshotScheduler
		self.snapshot_scheduler = SnapshotScheduler(bot_ref=self)
	
	async def _reconnect_with_retry(self, was_feature_1_active: bool):
		"""연결 끊김 이후 토큰 발급/웹소켓 재연결을 재시도합니다."""
		attempt = 0
		delay = 1.0
		max_delay = 60.0
		
		while True:
			attempt += 1
			
			# 재로그인 중이면 조용히 종료 (다른 로직이 처리)
			if hasattr(self.websocket, 'is_relogging') and self.websocket.is_relogging:
				print("재로그인 중이므로 재연결 재시도 루프를 종료합니다.")
				return
			
			try:
				token = await self.token_manager.get_token()
				if not token:
					print(f"토큰 발급 실패, {delay:.1f}초 후 재시도합니다... ({attempt}회)")
					await asyncio.sleep(delay)
					delay = min(delay * 1.5, max_delay)
					continue
				
				success = await self.websocket.start(token)
				if not success:
					print(f"웹소켓 재연결 실패, {delay:.1f}초 후 재시도합니다... ({attempt}회)")
					await asyncio.sleep(delay)
					delay = min(delay * 1.5, max_delay)
					continue
				
				# 재연결 성공: 필요 시 기능 1 복구
				if was_feature_1_active:
					try:
						await self.background_task_manager.start_features([1], token)
					except Exception as e:
						print(f"재연결 후 기능 1 복구 중 오류: {type(e).__name__}: {e}")

				# ★ 5/27 사고 회피: 재연결 후 holdings 0B 재등록 (손절 모니터링 복구).
				# 매수 직후 WebSocket 끊긴 케이스 — 재연결만 하고 0B 등록 안 하면
				# holdings_manager.on_0b_quote 호출 안 됨 → 손절 무작동.
				try:
					await self.holdings_manager._register_existing_holdings_0b()
				except Exception as e:
					print(f"재연결 후 holdings 0B 재등록 오류: {type(e).__name__}: {e}")

				# 장 시간 외에는 메시지를 보내지 않음
				if MarketHour.is_market_open_time():
					await tel_send("✅ 서버 연결이 복구되었습니다.")
				return
			
			except Exception as e:
				print(f"재연결 재시도 중 오류, {delay:.1f}초 후 재시도합니다... ({attempt}회): {type(e).__name__}: {e}")
				await asyncio.sleep(delay)
				delay = min(delay * 1.5, max_delay)
	
	async def _on_connection_closed(self):
		"""WebSocket 연결이 종료되었을 때 호출되는 콜백 함수"""
		try:
			# 재로그인 중이면 알림과 기능 중지를 건너뜀 (장 시간 전 재로그인)
			# 여러 번 확인하여 확실하게 체크
			if hasattr(self.websocket, 'is_relogging') and self.websocket.is_relogging:
				print("재로그인 중이므로 연결 종료 알림과 기능 중지를 건너뜁니다.")
				return
			
			# 재확인: 재로그인 중이면 즉시 리턴
			if self.websocket.is_relogging:
				print("재로그인 중이므로 연결 종료 알림과 기능 중지를 건너뜁니다. (재확인)")
				return
			
			# 장 시간 외에는 메시지를 보내지 않음
			is_market_time = MarketHour.is_market_open_time()
			
			print("WebSocket 연결이 종료되어 자동으로 재연결을 시도합니다.")
			if is_market_time:
				await tel_send("⚠️ 서버 연결이 끊어져 자동으로 재연결을 시도합니다.")
			# 기능 1이 활성화되어 있었는지 여부를 중지 전에 저장
			was_feature_1_active = 1 in self.background_task_manager.active_features
			# 기능 1만 중지
			await self.background_task_manager.stop_features([1])
			
			print("1초 후 재연결을 시도합니다.")
			await asyncio.sleep(1)
			
			# 재연결 재시도 태스크가 이미 돌고 있으면 중복 실행 방지
			if self._reconnect_task and not self._reconnect_task.done():
				print("이미 재연결 재시도 루프가 실행 중입니다.")
				return
			
			# 토큰 발급/웹소켓 재연결을 실패해도 계속 재시도
			self._reconnect_task = asyncio.create_task(
				self._reconnect_with_retry(was_feature_1_active)
			)
		except Exception as e:
			print(f"연결 종료 콜백 실행 중 오류: {e}")
			# 재로그인 중이 아니고 장 시간일 때만 오류 알림 발송
			if not (hasattr(self.websocket, 'is_relogging') and self.websocket.is_relogging) and MarketHour.is_market_open_time():
				await tel_send(f"❌ 연결 종료 처리 중 오류가 발생했습니다: {e}")
	
	async def _on_relogin_complete(self, new_token, active_features_before_relogin):
		"""재로그인이 완료되었을 때 호출되는 콜백 함수"""
		try:
			# BackgroundTaskManager에 재로그인 완료 알림
			await self.background_task_manager.on_relogin_complete(new_token, active_features_before_relogin)
		except Exception as e:
			print(f"재로그인 완료 콜백 실행 중 오류: {e}")

		# ★ 5/27 사고 회피: 재로그인 후 holdings 0B 재등록 (손절 모니터링 복구).
		# 08:59 _relogin_scheduler → 새 토큰 + 새 WebSocket이면 옛 0B 등록 무효화 가능.
		try:
			await self.holdings_manager._register_existing_holdings_0b()
		except Exception as e:
			print(f"재로그인 후 holdings 0B 재등록 오류: {type(e).__name__}: {e}")
	
	async def start(self, is_paper_trading=True, feature_numbers=None):
		"""start 명령어를 처리합니다."""
		return await start_command(
			self.websocket,
			self.token_manager,
			self.settings_manager,
			self.background_task_manager,
			is_paper_trading,
			feature_numbers
		)
	
	async def stop(self, set_auto_start_false=True, feature_numbers=None):
		"""stop 명령어를 처리합니다."""
		return await stop_command(
			self.websocket,
			self.settings_manager,
			self.background_task_manager,
			set_auto_start_false,
			feature_numbers
		)
	
	async def report(self):
		"""report 명령어를 처리합니다 - 보유종목과 선정된 종목의 수익율 조회"""
		return await report_command(
			self.token_manager,
			self.settings_manager,
			self.background_task_manager
		)
	
	async def condition(self, numbers=None):
		"""condition/cond 명령어를 처리합니다 - 조건식 목록 조회 또는 search_seq 설정"""
		return await condition_command(
			self.token_manager,
			self.settings_manager,
			self.websocket,
			self.background_task_manager,
			self.start,
			self.stop,
			numbers
		)
	
	async def top(self, number):
		"""top 명령어를 처리합니다 - stock_count 수정"""
		return await top_command(
			self.settings_manager,
			self.background_task_manager,
			self.start,
			self.stop,
			number
		)
	
	async def tpr(self, number):
		"""tpr 명령어를 처리합니다 - take_profit_rate 수정"""
		return await tpr_command(self.settings_manager, number)
	
	async def slr(self, number):
		"""slr 명령어를 처리합니다 - stop_loss_rate 수정"""
		return await slr_command(self.settings_manager, number)
	
	async def gapup(self, number):
		"""gapup 명령어 - pick 갭상승 차단 % (예: gapup 7)"""
		return await gapup_command(self.settings_manager, number)

	async def gapdown(self, number):
		"""gapdown 명령어 - pick 갭하락 차단 % (예: gapdown 5)"""
		return await gapdown_command(self.settings_manager, number)

	async def touch_rate(self, number):
		"""touch_rate 명령어 - touch 반등 임계값 % (예: touch_rate 10)"""
		return await touch_rate_command(self.settings_manager, number)

	async def brt(self, number):
		"""brt 명령어를 처리합니다 - buy_ratio 수정"""
		return await brt_command(self.settings_manager, number)
	
	async def bft(self, number):
		"""bft 명령어를 처리합니다 - buy_fixed_amount 수정"""
		return await bft_command(self.settings_manager, number)
	
	async def bftx(self, number):
		"""bftx 명령어를 처리합니다 - buy_fixed_amount 수정 및 고정 금액 엄격 모드 설정"""
		return await bftx_command(self.settings_manager, number)
	
	async def market(self, start_time_str, end_time_str):
		"""market 명령어를 처리합니다 - 장 시작/종료 시간 설정"""
		return await market_command(self.settings_manager, start_time_str, end_time_str)
	
	async def btp(self, order_type):
		"""btp 명령어를 처리합니다 - buy_order_type 수정"""
		return await btp_command(self.settings_manager, order_type)
	
	async def bto(self, args_str):
		"""bto 명령어를 처리합니다 - buy_timeout 및 buy_timeout_action 설정"""
		return await bto_command(self.settings_manager, args_str)
	
	async def cooldown(self, number):
		"""cooldown 명령어를 처리합니다 - sell_cooldown_hours 수정"""
		return await cooldown_command(self.settings_manager, number)
	
	async def tsr(self, number):
		"""tsr 명령어를 처리합니다 - trailing_stop_rate / trailing_min_profit 수정"""
		return await tsr_command(self.settings_manager, number)
	
	async def maxholdings(self, number):
		"""maxholdings 명령어를 처리합니다 - max_holdings 수정"""
		return await maxholdings_command(self.settings_manager, number)
	
	async def block_add(self, pattern):
		"""block add 명령어를 처리합니다 - 자동매매 금지 목록에 패턴 추가"""
		return await block_add_command(self.settings_manager, pattern)
	
	async def block_remove(self, pattern):
		"""block remove 명령어를 처리합니다 - 자동매매 금지 목록에서 패턴 제거"""
		return await block_remove_command(self.settings_manager, pattern)
	
	async def block_list(self):
		"""block list 명령어를 처리합니다 - 현재 자동매매 금지 목록 조회"""
		return await block_list_command(self.settings_manager)
	
	async def brk_rate(self, number):
		"""brk rate 명령어를 처리합니다 - 돌파율 설정"""
		return await brk_rate_command(self.settings_manager, number)
	
	async def brk_add(self, pattern):
		"""brk add 명령어를 처리합니다 - 돌파 감시 목록 추가"""
		return await brk_add_command(self.settings_manager, pattern)
	
	async def brk_remove(self, pattern):
		"""brk remove 명령어를 처리합니다 - 돌파 감시 목록 제거"""
		return await brk_remove_command(self.settings_manager, pattern)
	
	async def brk_list(self):
		"""brk list 명령어를 처리합니다 - 돌파 감시 목록 조회"""
		return await brk_list_command(self.settings_manager, self.token_manager)
	
	async def chart(self, x, y):
		"""chart 명령어를 처리합니다 - 차트 설정 수정"""
		# 기능 3 또는 4가 활성화되어 있는지 확인
		if 3 not in self.background_task_manager.active_features and 4 not in self.background_task_manager.active_features:
			await tel_send("❌ 차트 설정은 기능 3(골든크로스 매수) 또는 기능 4(데드크로스 매도)가 활성화되어 있을 때만 사용할 수 있습니다.")
			return False
		
		return await chart_command(
			self.settings_manager,
			self.background_task_manager,
			self.start,
			self.stop,
			x,
			y
		)
	
	async def help(self, command=None):
		"""help 명령어를 처리합니다 - 명령어 설명 및 사용법 가이드"""
		return await help_command(command)
	
	async def sell(self, stk_cd):
		"""sell 명령어를 처리합니다 - 보유 종목 중 선택한 종목을 매도"""
		return await sell_command(self.token_manager, stk_cd)
	
	async def sell_all(self):
		"""sellall 명령어를 처리합니다 - 보유 중인 모든 종목을 시장가로 매도"""
		return await sell_all_command(self.token_manager)
	
	async def search(self, stock_code):
		"""srch 명령어를 처리합니다 - 종목 정보 조회"""
		return await search_command(self.token_manager, stock_code)
	
	async def rank(self, rank_type):
		"""rank 명령어를 처리합니다 - 실시간 시장 순위 조회"""
		return await rank_command(self.token_manager, rank_type)
	
	async def setting(self):
		"""setting 명령어를 처리합니다 - 현재 모든 설정 상태 출력"""
		try:
			# 모든 설정 값 가져오기
			auto_start = self.settings_manager.get_setting('auto_start', False)
			is_paper_trading = self.settings_manager.get_setting('is_paper_trading', True)
			search_seq = self.settings_manager.get_setting('search_seq', '0')
			stock_count = self.settings_manager.get_setting('stock_count', 10)
			chart_short = self.settings_manager.get_setting('chart_short', 5)
			chart_long = self.settings_manager.get_setting('chart_long', 20)
			take_profit_rate = self.settings_manager.get_setting('take_profit_rate', 5.0)
			stop_loss_rate = self.settings_manager.get_setting('stop_loss_rate', -5.0)
			buy_ratio = self.settings_manager.get_setting('buy_ratio', 2.0)
			buy_mode = self.settings_manager.get_setting('buy_mode', 'ratio')
			buy_fixed_amount = self.settings_manager.get_setting('buy_fixed_amount', 100000)
			buy_order_type = self.settings_manager.get_setting('buy_order_type', 'limit')
			buy_timeout = self.settings_manager.get_setting('buy_timeout', 0)
			buy_timeout_action = self.settings_manager.get_setting('buy_timeout_action', 'cancel')
			market_start_hour = self.settings_manager.get_setting('market_start_hour', 9)
			market_start_minute = self.settings_manager.get_setting('market_start_minute', 0)
			market_end_hour = self.settings_manager.get_setting('market_end_hour', 15)
			market_end_minute = self.settings_manager.get_setting('market_end_minute', 30)
			last_feature_numbers = self.settings_manager.get_setting('last_feature_numbers', None)
			sell_cooldown_hours = self.settings_manager.get_setting('sell_cooldown_hours', 24)
			trailing_stop_rate = self.settings_manager.get_setting('trailing_stop_rate', 3.0)
			trailing_min_profit = self.settings_manager.get_setting('trailing_min_profit', 0.0)
			max_holdings = self.settings_manager.get_setting('max_holdings', 0)
			auto_sell_blocklist = self.settings_manager.get_setting('auto_sell_blocklist', [])
			break_rate = self.settings_manager.get_setting('break_rate', 3.0)
			break_stock_list = self.settings_manager.get_setting('break_stock_list', [])
			if not isinstance(auto_sell_blocklist, list):
				auto_sell_blocklist = []
			if not isinstance(break_stock_list, list):
				break_stock_list = []
			
			# 설정 메시지 포맷팅
			message = "⚙️ [현재 설정 상태]\n\n"
			
			# 기본 설정
			message += "📋 [기본 설정]\n"
			message += f"  자동 시작: {'✅' if auto_start else '❌'}\n"
			message += f"  거래 모드: {'모의투자' if is_paper_trading else '실제투자'}\n"
			message += f"  마지막 기능 조합: {last_feature_numbers if last_feature_numbers else '없음'}\n\n"
			
			# 기능 1 설정
			message += "🔍 [기능 1: 조건식 검색 매수]\n"
			# search_seq가 리스트인 경우 처리
			if isinstance(search_seq, list):
				seq_str = ", ".join(search_seq)
				message += f"  검색 조건식 번호: {seq_str}\n\n"
			else:
				message += f"  검색 조건식 번호: {search_seq}\n\n"
			
			# 기능 2 설정
			message += "💰 [기능 2: 수익율 매도]\n"
			message += f"  익절 기준: {take_profit_rate}%\n"
			message += f"  손절 기준: {stop_loss_rate}%\n\n"
			
			# 기능 3 설정
			message += "📈 [기능 3: 골든크로스 매수]\n"
			message += f"  종목 선정 개수: {stock_count}개\n"
			message += f"  단기 분봉: {chart_short}분\n"
			message += f"  장기 분봉: {chart_long}분\n\n"
			
			# 기능 4 설정
			message += "📉 [기능 4: 데드크로스 매도]\n"
			message += f"  단기 분봉: {chart_short}분\n"
			message += f"  장기 분봉: {chart_long}분\n\n"
			
			# 기능 5 설정
			message += "📉 [기능 5: 트레일링 스탑 매도]\n"
			message += f"  트레일링 스탑 퍼센티지: {trailing_stop_rate}%\n"
			message += f"  최소 발동 수익률: {trailing_min_profit}%\n\n"
			
			# 기능 6 설정
			message += "🚀 [기능 6: 돌파 매수]\n"
			message += f"  돌파율: {break_rate}%\n"
			if len(break_stock_list) == 0:
				message += "  감시 종목: 비어있음\n\n"
			else:
				message += f"  감시 종목: {', '.join(break_stock_list)}\n\n"
			
			# 기능 7 설정
			from telegram.commands.grid_command import load_grid_status
			grid_data = load_grid_status(self.script_dir)
			message += "📊 [기능 7: 그리드 트레이딩]\n"
			if len(grid_data) == 0:
				message += "  등록된 종목: 비어있음\n\n"
			else:
				message += f"  등록된 종목: {len(grid_data)}개\n"
				for stock_code, config in list(grid_data.items())[:3]:  # 최대 3개만 표시
					message += f"    - {stock_code}: {config.get('current_step', 1)}/{config.get('max_steps', 1)}단계\n"
				if len(grid_data) > 3:
					message += f"    ... 외 {len(grid_data) - 3}개\n"
				message += "\n"
			
			# 기능 8 설정
			from telegram.commands.wave_command import load_wave_config, load_wave_status
			wave_config = load_wave_config(self.script_dir)
			wave_status = load_wave_status(self.script_dir)
			buy_steps = wave_config.get('buy_steps', [5.0, 10.0, 3.0])
			sell_steps = wave_config.get('sell_steps', [5.0, 10.0, 3.0])
			message += "🌊 [기능 8: 분할 트레이딩]\n"
			message += f"  매수 단계: {buy_steps[0]}%, {buy_steps[1]}%, {buy_steps[2]}%\n"
			message += f"  매도 단계: {sell_steps[0]}%, {sell_steps[1]}%, {sell_steps[2]}%\n"
			if len(wave_status) == 0:
				message += "  감시 종목: 비어있음\n\n"
			else:
				message += f"  감시 종목: {len(wave_status)}개\n"
				for stock_code, status in list(wave_status.items())[:3]:  # 최대 3개만 표시
					stock_name = status.get('name', stock_code)
					phase = status.get('current_phase', 'BUY')
					step_index = status.get('step_index', 0)
					message += f"    - {stock_name}({stock_code}): {phase} {step_index + 1}차\n"
				if len(wave_status) > 3:
					message += f"    ... 외 {len(wave_status) - 3}개\n"
				message += "\n"
			
			# 매수 설정
			message += "🛒 [매수 설정]\n"
			if buy_mode == 'fixed_strict':
				message += f"  매수 모드: 고정 금액(엄격)\n"
				message += f"  고정 금액: {int(buy_fixed_amount):,}원\n"
			elif buy_mode == 'fixed':
				message += f"  매수 모드: 고정 금액\n"
				message += f"  고정 금액: {int(buy_fixed_amount):,}원\n"
			else:
				message += f"  매수 모드: 비율\n"
				message += f"  매수 비율: {buy_ratio}%\n"
			
			# 주문 타입 표시
			if buy_order_type == 'market':
				order_type_str = '시장가'
			elif buy_order_type == 'limit' or buy_order_type == '0':
				order_type_str = '보통가(지정가)'
			else:
				try:
					ticks = int(buy_order_type)
					order_type_str = f'{ticks}호가 낮춤 (지정가)'
				except (ValueError, TypeError):
					order_type_str = '보통가(지정가)'
			message += f"  주문 타입: {order_type_str}\n"
			
			# 타임아웃 설정 표시
			if buy_timeout == 0:
				message += f"  타임아웃: 비활성화\n\n"
			else:
				action_str = '취소' if buy_timeout_action == 'cancel' else '시장가 전환'
				message += f"  타임아웃: {buy_timeout}초 후 {action_str}\n\n"
			
			# 장 시간 설정
			message += "⏰ [장 시간 설정]\n"
			message += f"  시작 시간: {market_start_hour:02d}:{market_start_minute:02d}\n"
			message += f"  종료 시간: {market_end_hour:02d}:{market_end_minute:02d}\n\n"
			
			# 쿨다운 설정
			message += "⏸️ [매도 후 재매수 쿨다운]\n"
			if sell_cooldown_hours == 0:
				message += f"  쿨다운: 비활성화\n"
			else:
				# 시간과 분으로 변환
				whole_hours = int(sell_cooldown_hours)
				minutes = int((sell_cooldown_hours - whole_hours) * 60)
				
				# 메시지 포맷팅
				if whole_hours > 0 and minutes > 0:
					time_str = f"{whole_hours}시간 {minutes}분"
				elif whole_hours > 0:
					time_str = f"{whole_hours}시간"
				elif minutes > 0:
					time_str = f"{minutes}분"
				else:
					time_str = "0분"
				
				message += f"  쿨다운 시간: {time_str}\n\n"
			
			# 보유종목 개수 제한 설정
			message += "📊 [보유종목 개수 제한]\n"
			if max_holdings == 0:
				message += f"  제한: 비활성화 (제한 없음)\n\n"
			else:
				message += f"  최대 보유 개수: {max_holdings}개\n\n"
			
			# 자동매매 금지 목록 설정
			message += "🚫 [자동매매 금지 목록]\n"
			if len(auto_sell_blocklist) == 0:
				message += f"  금지 목록: 비어있음\n"
			else:
				blocklist_str = ", ".join(auto_sell_blocklist)
				message += f"  금지 목록: {blocklist_str}\n"
			
			# 미체결 주문 일괄 취소 설정
			from telegram.commands.cancel_unfilled_command import get_scheduler
			scheduler = get_scheduler(self.token_manager)
			ccu_status = scheduler.get_status()
			message += "\n🔄 [미체결 주문 일괄 취소]\n"
			if ccu_status:
				message += f"  상태: {ccu_status}\n"
			else:
				message += f"  상태: 비활성화\n"
			
			await tel_send(message)
			return True
			
		except Exception as e:
			await tel_send(f"❌ setting 명령어 실행 중 오류: {e}")
			return False
	
	# ─────────────────────────────────────────────────────────
	# Phase 2 Step B — pick/cancel/status 헬퍼 (영속화 함수 사용)
	# ─────────────────────────────────────────────────────────
	async def _cmd_pick(self, args: list) -> bool:
		"""매수 후보 종목을 buy_queue(파일 영속화)에 추가.

		사용법 (원본 buy_command 위치인자 방식):
		  pick <종목코드>          → 1주
		  pick <종목코드> <수량>   → 지정 수량 (예: pick 005930 5 → 5주)

		이미 보유 중인 종목(pending_fill / filled)은 등록 차단.
		"""
		from utils.collection_pool import get_stock_name
		from utils.buy_queue import add_to_queue, load_queue
		from utils.holdings import load_holdings

		if not args:
			await tel_send("❌ 사용법: pick <종목코드> [수량]\n예: pick 005930 5")
			return False

		# 종목코드 검증 (원본 buy_command과 동일)
		code = str(args[0]).strip()
		import re as _re
		if not _re.match(r'^[\dA-Z]{6}$', code.upper()) or len(code) != 6:
			await tel_send(f"❌ 종목코드는 6자리 (숫자 또는 영문 대문자)여야 합니다. (입력: {code})")
			return False
		code = code.upper()

		# 수량 파싱 (원본 buy_command Case 2 패턴)
		qty = 1
		if len(args) >= 2:
			try:
				qty = int(args[1])
				if qty <= 0:
					await tel_send("❌ 수량은 1 이상이어야 합니다.")
					return False
			except (ValueError, TypeError):
				await tel_send(f"❌ 수량은 숫자여야 합니다. (입력: {args[1]})")
				return False

		# 현재 보유 종목 조회 — 매수 대상 차단용
		held = await load_holdings()
		held_codes = {h['code'] for h in held if h.get('status') in ('pending_fill', 'filled')}
		if code in held_codes:
			await tel_send(f"⚠️ {code} 이미 보유 중 — 매수 후보 등록 차단")
			return False

		name = await get_stock_name(code)
		label = f"{code} {name}" if name else code

		if await add_to_queue(code, approved_by='telegram', qty=qty):
			queue = await load_queue()
			await tel_send(
				f"✅ [매수 후보 승인] {label} {qty}주\n"
				f"📦 매수 대기열 총 {len(queue)}건"
			)
		else:
			queue = await load_queue()
			await tel_send(
				f"♻️ {label} 이미 매수 대기열에 있음\n"
				f"📦 매수 대기열 총 {len(queue)}건"
			)
		return True

	async def _cmd_cancel(self, code: str) -> bool:
		"""buy_queue에서 특정 종목 제거 (영속화)."""
		from utils.buy_queue import remove_from_queue, load_queue
		c = str(code).strip().upper()
		ok = await remove_from_queue(c)
		queue = await load_queue()
		if not ok:
			await tel_send(f"❌ [취소 실패] {c} 는 매수 대기열에 없음 (현재 {len(queue)}건)")
			return False
		await tel_send(f"🗑️ [취소] {c} 제거 — 매수 대기열 {len(queue)}건")
		return True

	async def _cmd_watching(self) -> bool:
		"""watching 큐 종목 목록 + 상태 출력."""
		from utils.buy_queue_watching import load_watching
		from datetime import datetime

		entries = await load_watching()
		if not entries:
			await tel_send("👀 [감시 종목] 없음")
			return True

		lines = [f"👀 [감시 종목] {len(entries)}건"]
		now = datetime.now()
		for it in entries:
			code = it.get('code', '-')
			reason = it.get('block_reason', '-')
			ratio = it.get('block_ratio')
			ratio_str = f"{(ratio - 1) * 100:+.1f}%" if isinstance(ratio, (int, float)) else "-"
			normal_since = it.get('normal_since')
			if normal_since:
				try:
					elapsed_min = int((now - datetime.fromisoformat(normal_since)).total_seconds() / 60)
					state = f"정상 진입 {elapsed_min}분 경과 (30분 도달 시 매수)"
				except Exception:
					state = "정상 진입 (시각 파싱 실패)"
			else:
				state = "차단 범위 대기 중"
			failed_count = it.get('consecutive_failed_count') or 0
			fail_str = f" / 실패 {failed_count}회" if failed_count else ""
			lines.append(f"- {code} ({reason} {ratio_str}) {state}{fail_str}")
		await tel_send("\n".join(lines))
		return True

	async def _cmd_watching_cancel(self, code: str) -> bool:
		"""watching 큐에서 특정 종목 제거."""
		from utils.buy_queue_watching import remove_from_watching, load_watching
		c = str(code).strip().upper()
		ok = await remove_from_watching(c)
		entries = await load_watching()
		if not ok:
			await tel_send(f"❌ [감시 취소 실패] {c} 는 감시 큐에 없음 (현재 {len(entries)}건)")
			return False
		await tel_send(f"🗑️ [감시 취소] {c} 제거 — 감시 큐 {len(entries)}건")
		return True

	async def _cmd_auction(self, args: list) -> bool:
		"""동시호가 매수 등록 — 다음 거래일 08:30~08:50 시장가 자동 매수.

		사용법: auction <종목코드> [수량]
		예: auction 005930 5

		Lee 6/2: 필터 없이 시장가 매수 (자동매매 금지·쿨다운만 안전망).
		갭상승/하락 우회 — 미국 폭등 시 한국 동시호가 진입용.
		"""
		from utils.collection_pool import get_stock_name
		from utils.buy_queue import add_to_queue, load_queue

		if not args:
			await tel_send("❌ 사용법: auction <종목코드> [수량]\n예: auction 005930 5")
			return False

		code = str(args[0]).strip()
		import re as _re
		if not _re.match(r'^[\dA-Z]{6}$', code.upper()) or len(code) != 6:
			await tel_send(f"❌ 종목코드는 6자리 (숫자 또는 영문 대문자)여야 합니다. (입력: {code})")
			return False
		code = code.upper()

		qty = 1
		if len(args) >= 2:
			try:
				qty = int(args[1])
				if qty <= 0:
					await tel_send("❌ 수량은 1 이상이어야 합니다.")
					return False
			except (ValueError, TypeError):
				await tel_send(f"❌ 수량은 숫자여야 합니다. (입력: {args[1]})")
				return False

		name = await get_stock_name(code)
		label = f"{code} {name}" if name else code

		if await add_to_queue(code, approved_by='telegram', qty=qty, source='auction'):
			queue = await load_queue()
			await tel_send(
				f"🔥 [auction 등록] {label} {qty}주\n"
				f"다음 거래일 08:30~08:50 동시호가 시장가 매수\n"
				f"📦 매수 대기열 총 {len(queue)}건"
			)
			return True
		else:
			await tel_send(f"♻️ {label} 이미 매수 대기열에 있음")
			return False

	async def _cmd_touch(self, args: list) -> bool:
		"""touch 명령 — 일중 최저점 반등 매수.

		cur >= low + touch_rate% × (open - low) 시점에 시장가 매수.
		장 중 09:00 ~ 15:20 30초 polling. 매수 1회 후 큐 제거.
		"""
		from utils.collection_pool import get_stock_name
		from utils.buy_queue import add_to_queue, load_queue
		from utils.get_setting import get_setting

		if not args:
			await tel_send("❌ 사용법: touch <종목코드> [수량]\n예: touch 005930 1")
			return False

		code = str(args[0]).strip()
		import re as _re
		if not _re.match(r'^[\dA-Z]{6}$', code.upper()) or len(code) != 6:
			await tel_send(f"❌ 종목코드는 6자리 (숫자 또는 영문 대문자)여야 합니다. (입력: {code})")
			return False
		code = code.upper()

		qty = 1
		if len(args) >= 2:
			try:
				qty = int(args[1])
				if qty <= 0:
					await tel_send("❌ 수량은 1 이상이어야 합니다.")
					return False
			except (ValueError, TypeError):
				await tel_send(f"❌ 수량은 숫자여야 합니다. (입력: {args[1]})")
				return False

		name = await get_stock_name(code)
		label = f"{code} {name}" if name else code
		rate = float(get_setting('touch_rate', 10.0))

		if await add_to_queue(code, approved_by='telegram', qty=qty, source='touch'):
			queue = await load_queue()
			touch_count = sum(1 for q in queue if q.get('source') == 'touch')
			await tel_send(
				f"🎯 [touch 등록] {label} {qty}주\n"
				f"트리거: 시가-최저점 차이의 {rate}% 이상 반등 시 시장가 매수\n"
				f"감시: 0B 실시간 push (장 중 09:00 ~ 15:20)\n"
				f"📦 touch 대기 {touch_count}건"
			)
			# 0B 등록 + 즉시 1회 트리거 체크 (캐시 채움 겸)
			import asyncio as _asyncio
			await self.touch_executor.register_for_touch(code)
			_asyncio.create_task(self.touch_executor._check_touches())
			return True
		else:
			await tel_send(f"♻️ {label} 이미 매수 대기열에 있음")
			return False

	async def _cmd_touch_list(self) -> bool:
		"""touch 큐 조회 + 종목별 시가/저가/하락폭 + 조건3 충족 여부."""
		from utils.buy_queue import load_queue
		from utils.collection_pool import get_stock_name
		from utils.get_setting import get_setting
		from api.stock_info import fn_ka10001

		queue = await load_queue()
		touches = [q for q in queue if q.get('source') == 'touch']
		rate = float(get_setting('touch_rate', 10.0))
		min_drop = float(get_setting('touch_min_drop_pct', 5.0))
		if not touches:
			await tel_send(f"📦 touch 대기열 비어있음 (반등 {rate}% / 최소 하락 {min_drop}%)")
			return True

		lines = [f"🎯 [touch 대기열] {len(touches)}건 (반등 {rate}% / 최소 하락 {min_drop}%)"]
		token = await self.token_manager.get_token()
		for t in touches:
			c = t.get('code', '-')
			q = t.get('qty', 1)
			name = await get_stock_name(c)
			# 시가/저가 조회 → 현재 하락폭 + 조건3 충족 여부
			info_str = ""
			try:
				info = await fn_ka10001(c, token=token, silent=True)
				raw = info.get('raw', {}) if isinstance(info, dict) else {}
				op_raw = str(raw.get('open_pric', '0')).lstrip('-')
				lo_raw = str(raw.get('low_pric', '0')).lstrip('-')
				op = float(op_raw or 0)
				lo = float(lo_raw or 0)
				cur = float(info.get('cur_prc') or 0)
				if op > 0 and lo > 0:
					drop_pct = (op - lo) / op * 100.0
					cond3 = "✅" if drop_pct >= min_drop else "❌"
					trig = lo + (rate / 100.0) * (op - lo) if op > lo else 0
					arrow = "🟢" if cur >= trig and trig > 0 else "⚪"
					info_str = f"\n    시={int(op):,} 저={int(lo):,} 현={int(cur):,} 하락폭 {drop_pct:.2f}% {cond3} 트리거 {int(trig):,} {arrow}"
			except Exception:
				info_str = "\n    (시세 조회 실패)"
			lines.append(f"  • {c} {name or ''} {q}주{info_str}")
		await tel_send("\n".join(lines))
		return True

	async def _cmd_auction_cancel(self, code: str) -> bool:
		"""auction 큐에서 종목 제거 (touch_cancel과 대칭)."""
		from utils.buy_queue import load_queue, remove_from_queue
		c = str(code).strip().upper()
		queue = await load_queue()
		target = next((q for q in queue if q.get('code') == c and q.get('source') == 'auction'), None)
		if not target:
			await tel_send(f"❌ {c} 는 auction 대기열에 없음")
			return False
		await remove_from_queue(c, source='auction')
		await tel_send(f"🗑️ [auction 취소] {c}")
		return True

	async def _cmd_auction_list(self) -> bool:
		"""auction 큐 조회."""
		from utils.buy_queue import load_queue
		from utils.collection_pool import get_stock_name

		queue = await load_queue()
		auctions = [q for q in queue if q.get('source') == 'auction']
		if not auctions:
			await tel_send("📦 auction 대기열 비어있음")
			return True
		lines = [f"🔥 [auction 대기열] {len(auctions)}건"]
		for a in auctions:
			c = a.get('code', '-')
			q = a.get('qty', 1)
			name = await get_stock_name(c)
			lines.append(f"  • {c} {name or ''} {q}주 (등록 {a.get('approved_at','-')})")
		await tel_send("\n".join(lines))
		return True

	async def _cmd_touch_cancel(self, code: str) -> bool:
		"""touch 큐에서 종목 제거."""
		from utils.buy_queue import load_queue, remove_from_queue
		c = str(code).strip().upper()
		queue = await load_queue()
		target = next((q for q in queue if q.get('code') == c and q.get('source') == 'touch'), None)
		if not target:
			await tel_send(f"❌ {c} 는 touch 대기열에 없음")
			return False
		await remove_from_queue(c, source='touch')
		await tel_send(f"🗑️ [touch 취소] {c}")
		return True

	async def _cmd_score(self) -> bool:
		"""semi_trigger 5축 + semi + legacy 즉시 조회 (DB write 포함).

		Lee 수동 판단용 — 자동 02:00/05:30 외에 임의 시점에 호출.
		"""
		from modules.semi_trigger.snapshot import take_snapshot, resolve_eval_date

		token = await self.token_manager.get_token()
		if not token:
			await tel_send("❌ 토큰 발급 실패 — score 조회 불가")
			return False
		eval_date = resolve_eval_date()
		if not eval_date:
			await tel_send(
				"⚠️ [score] daily_factors 비어있음\n"
				"어제 16:00 daily_analyzer evening pipeline 실행 여부 확인 필요"
			)
			return False
		try:
			await take_snapshot(token=token, eval_date=eval_date,
			                    label='manual', send_telegram=True)
			return True
		except Exception as e:
			logger.exception("[score] snapshot 실패")
			await tel_send(f"❌ score 조회 중 오류: {e}")
			return False

	async def _cmd_holdings_clean(self, code: str) -> bool:
		"""봇 holdings.json에서 잔재 entry 제거 (계좌 실제 보유 없는 종목).

		용도: Feature 2 매도 시 자동 갱신 안 되던 옛 버그로 남은 잔재, 또는
		Lee가 HTS에서 수동 매도 후 봇 holdings 정합성 회복.
		영구 원칙 #30 준수 — 봇 데몬 내부에서만 실행.
		"""
		from utils.holdings import remove_holding, load_holdings
		c = str(code).strip().upper()
		removed = await remove_holding(c)
		holdings = await load_holdings()
		if removed is None:
			await tel_send(f"❌ {c} 는 holdings.json에 없음 (현재 {len(holdings)}건)")
			return False
		await tel_send(
			f"🗑️ [holdings 청소] {c} 제거 — {removed.get('buy_qty', '-')}주 "
			f"@ {removed.get('buy_price', 0):,}원 ({removed.get('buy_date', '-')})\n"
			f"holdings.json 총 {len(holdings)}건"
		)
		return True

	async def _cmd_stick(self, args: list) -> bool:
		"""stick 종목 영구 등록 — 매일 08:30 SOX/NQ 조건 충족 시 자동 매수.

		사용법: stick <종목코드> [수량] [tpr <n>] [slr <n>]
		  예: stick 122630         → 1주, 글로벌 tpr/slr 사용
		      stick 122630 5       → 5주
		      stick 122630 5 tpr 3 → 5주, 익절 +3% (slr는 글로벌)
		      stick 122630 5 tpr 3 slr 2 → 5주, +3% 익절 / -2% 손절
		"""
		from utils.collection_pool import get_stock_name
		from utils.stick_list import add_stick, load_stick

		if not args:
			await tel_send(
				"❌ 사용법: stick <종목코드> [수량] [tpr <n>] [slr <n>]\n"
				"예: stick 122630 5 tpr 3 slr 2"
			)
			return False

		code = str(args[0]).strip()
		import re as _re
		if not _re.match(r'^[\dA-Z]{6}$', code.upper()) or len(code) != 6:
			await tel_send(f"❌ 종목코드는 6자리 (숫자 또는 영문 대문자)여야 합니다. (입력: {code})")
			return False
		code = code.upper()

		# args[1:] 파싱 — 키워드 tpr/slr 추출, 나머지 첫 숫자는 수량
		qty = 1
		tpr = None
		slr = None
		i = 1
		while i < len(args):
			tok = str(args[i]).strip().lower()
			if tok == 'tpr' and i + 1 < len(args):
				try:
					tpr = float(args[i + 1])
					if tpr <= 0:
						await tel_send("❌ tpr은 1 이상이어야 합니다.")
						return False
				except (ValueError, TypeError):
					await tel_send(f"❌ tpr 값은 숫자여야 합니다. (입력: {args[i + 1]})")
					return False
				i += 2
			elif tok == 'slr' and i + 1 < len(args):
				try:
					slr = float(args[i + 1])
					if slr <= 0:
						await tel_send("❌ slr은 1 이상이어야 합니다 (양수 입력, 내부에서 음수 변환).")
						return False
				except (ValueError, TypeError):
					await tel_send(f"❌ slr 값은 숫자여야 합니다. (입력: {args[i + 1]})")
					return False
				i += 2
			elif tok.isdigit() and qty == 1:
				# 첫 번째 평순 숫자 = 수량
				qty = int(tok)
				if qty <= 0:
					await tel_send("❌ 수량은 1 이상이어야 합니다.")
					return False
				i += 1
			else:
				await tel_send(f"❌ 알 수 없는 인자: {args[i]}")
				return False

		name = await get_stock_name(code)
		label = f"{code} {name}" if name else code

		if not await add_stick(code, qty=qty, tpr=tpr, slr=slr):
			items = await load_stick()
			await tel_send(
				f"♻️ {label} 이미 stick 등록됨 — 변경하려면 stick_cancel 후 재등록\n"
				f"📋 stick 총 {len(items)}건"
			)
			return False

		items = await load_stick()
		tpr_str = f"+{tpr}%" if tpr is not None else "글로벌"
		slr_str = f"-{slr}%" if slr is not None else "글로벌"
		await tel_send(
			f"✅ [stick 등록] {label} {qty}주\n"
			f"   익절: {tpr_str} / 손절: {slr_str}\n"
			f"📋 stick 총 {len(items)}건 — 매일 08:30 SOX/NQ 체크 후 자동 매수"
		)
		return True

	async def _cmd_stick_cancel(self, code: str) -> bool:
		"""stick 등록 취소."""
		from utils.stick_list import remove_stick, load_stick
		c = str(code).strip()
		ok = await remove_stick(c)
		items = await load_stick()
		if not ok:
			await tel_send(f"❌ [stick 취소 실패] {c} 는 stick에 없음 (현재 {len(items)}건)")
			return False
		await tel_send(f"🗑️ [stick 취소] {c} 제거 — stick 총 {len(items)}건")
		return True

	async def _cmd_stick_list(self) -> bool:
		"""stick 등록 종목 + tpr/slr 출력."""
		from utils.collection_pool import get_stock_name
		from utils.stick_list import load_stick

		items = await load_stick()
		if not items:
			await tel_send("📋 [stick 등록 종목] 없음")
			return True

		lines = [f"📋 [stick 등록 종목] {len(items)}건"]
		for it in items:
			code = it.get('code', '-')
			qty = it.get('qty', 1)
			name = await get_stock_name(code)
			label = f"{code} {name}" if name else code
			tpr = it.get('tpr')
			slr = it.get('slr')
			tpr_str = f"+{tpr}%" if tpr is not None else "글로벌"
			slr_str = f"{slr}%" if slr is not None else "글로벌"
			lines.append(f"- {label} {qty}주 / 익절 {tpr_str} / 손절 {slr_str}")
		await tel_send("\n".join(lines))
		return True

	async def _cmd_status(self) -> bool:
		"""봇 상태 출력 — 매수 대기열, halt 여부 등."""
		from utils.collection_pool import get_stock_name, get_pool
		from utils.buy_queue import load_queue
		from datetime import datetime as _dt

		queue = await load_queue()
		pool = get_pool()
		pool_count = len(pool) if isinstance(pool, dict) else 0

		queue_lines = []
		for item in queue:
			c = item.get('code', '')
			n = await get_stock_name(c)
			queue_lines.append(f"  • {c} {n}".rstrip())
		queue_block = "\n".join(queue_lines) if queue_lines else "  (없음)"

		pool_lines = []
		if isinstance(pool, dict):
			for code in pool:
				pn = await get_stock_name(code)
				pool_lines.append(f"  • {code} {pn}".rstrip())
		pool_block = "\n".join(pool_lines) if pool_lines else "  (없음)"

		msg = (
			f"=== 봇 상태 ({_dt.now().strftime('%Y-%m-%d %H:%M:%S')}) ===\n"
			f"\n📦 매수 대기열 ({len(queue)}건):\n{queue_block}\n"
			f"\n⏸️ 매수 정지(halt): {'YES' if self.is_halted else 'NO'}\n"
			f"\n📥 수집풀 누적: {pool_count}종목\n{pool_block}"
		)
		await tel_send(msg)
		return True

	async def _cmd_force_daily(self) -> bool:
		"""[DEBUG] daily_analyzer 즉시 실행. Step B 검증 후 제거 예정."""
		try:
			await self.daily_analyzer.run()
			await tel_send("✅ [DEBUG] daily_analyzer 강제 실행 완료")
			return True
		except Exception as e:
			await tel_send(f"❌ [DEBUG] daily_analyzer 실패: {type(e).__name__}: {e}")
			return False

	async def process_command(self, text):
		"""텍스트 명령어를 처리합니다."""
		# background_task_manager의 콜백 설정 (처음 호출 시)
		if self.background_task_manager.process_command_callback is None:
			self.background_task_manager.process_command_callback = self.process_command
		
		# websocket의 process_command_callback 설정 (처음 호출 시, fcond 명령어 실행용)
		if self.websocket.process_command_callback is None:
			self.websocket.process_command_callback = self.process_command
		
		# 예약 스케줄러 초기화 (처음 호출 시)
		if self._reserve_scheduler is None:
			from telegram.commands.reserve_command import get_scheduler
			self._reserve_scheduler = get_scheduler(self.script_dir, self.process_command)
		else:
			# 이미 초기화된 경우 콜백 업데이트
			self._reserve_scheduler.update_callback(self.process_command)
		
		# 텍스트 trim 및 소문자 변환
		command = text.strip().lower()

		# Phase 2 Step A/B 명령 (pick/cancel/status/halt/resume + force_daily)
		# 매수 결정은 buy_queue.json에 영속화, 실제 09:00 매수 실행은 Step C에서 wiring.
		if command == 'status':
			return await self._cmd_status()
		elif command == 'halt':
			self.is_halted = True
			await tel_send("⏸️ [정지] 매수 전면 중단. resume 명령으로 재개.")
			return True
		elif command == 'resume':
			self.is_halted = False
			await tel_send("▶️ [재개] 매수 정상화. 다음 09:00 매수 가동 (Step C 통합 후).")
			return True
		elif command == 'force_daily':
			return await self._cmd_force_daily()
		elif command.startswith('pick '):
			parts = text.strip().split()[1:]  # 종목코드는 대소문자 무관하지만 원본 split 사용
			return await self._cmd_pick(parts)
		elif command.startswith('cancel '):
			parts = command.split()
			if len(parts) == 2:
				return await self._cmd_cancel(parts[1])
			else:
				await tel_send("❌ 사용법: cancel <종목코드>")
				return False
		elif command == 'watching':
			return await self._cmd_watching()
		elif command.startswith('watching_cancel '):
			parts = command.split()
			if len(parts) == 2:
				return await self._cmd_watching_cancel(parts[1])
			else:
				await tel_send("❌ 사용법: watching_cancel <종목코드>")
				return False
		elif command.startswith('holdings_clean '):
			parts = command.split()
			if len(parts) == 2:
				return await self._cmd_holdings_clean(parts[1])
			else:
				await tel_send("❌ 사용법: holdings_clean <종목코드>")
				return False
		elif command == 'score':
			return await self._cmd_score()
		elif command == 'auction_list':
			return await self._cmd_auction_list()
		elif command.startswith('auction_cancel '):
			parts = command.split()
			if len(parts) == 2:
				return await self._cmd_auction_cancel(parts[1])
			else:
				await tel_send("❌ 사용법: auction_cancel <종목코드>")
				return False
		elif command.startswith('auction '):
			parts = text.strip().split()[1:]
			return await self._cmd_auction(parts)
		elif command == 'stick_list':
			return await self._cmd_stick_list()
		elif command.startswith('stick_cancel '):
			parts = command.split()
			if len(parts) == 2:
				return await self._cmd_stick_cancel(parts[1])
			else:
				await tel_send("❌ 사용법: stick_cancel <종목코드>")
				return False
		elif command.startswith('stick '):
			parts = text.strip().split()[1:]
			return await self._cmd_stick(parts)
		elif command == 'touch_list':
			return await self._cmd_touch_list()
		elif command.startswith('touch_cancel '):
			parts = command.split()
			if len(parts) == 2:
				return await self._cmd_touch_cancel(parts[1])
			else:
				await tel_send("❌ 사용법: touch_cancel <종목코드>")
				return False
		elif command.startswith('touch_rate '):
			parts = command.split()
			if len(parts) == 2:
				return await self.touch_rate(parts[1])
			else:
				await tel_send("❌ 사용법: touch_rate {%} (예: touch_rate 10)")
				return False
		elif command.startswith('touch '):
			parts = text.strip().split()[1:]
			return await self._cmd_touch(parts)

		if command == 'start':
			return await self.start(is_paper_trading=True, feature_numbers=None)
		elif command == 'start real':
			return await self.start(is_paper_trading=False, feature_numbers=None)
		elif command.startswith('start '):
			parts = command.split()
			if len(parts) == 2:
				# "start 14", "start 234" 등 모든 조합 지원
				feature_str = parts[1]
				if feature_str.isdigit():
					return await self.start(is_paper_trading=True, feature_numbers=feature_str)
			elif len(parts) == 3 and parts[1] == 'real':
				# "start real 14" 형태도 지원
				feature_str = parts[2]
				if feature_str.isdigit():
					return await self.start(is_paper_trading=False, feature_numbers=feature_str)
			else:
				await tel_send("❌ 사용법: start {숫자조합} 또는 start real {숫자조합} (예: start 14, start real 234)")
				return False
		elif command == 'stop':
			return await self.stop(set_auto_start_false=True, feature_numbers=None)
		elif command == 'stop all':
			# 모든 기능 중지
			if not self.background_task_manager.is_running_any:
				await tel_send("⚠️ 현재 실행 중인 기능이 없습니다.")
				return True
			
			# auto_start 설정을 false로 변경
			if not self.settings_manager.update_setting('auto_start', False):
				await tel_send("❌ 설정 파일 업데이트 실패")
				return False
			
			# 모든 기능 중지
			await self.background_task_manager.stop_all()
			await tel_send("✅ 모든 기능이 중지되었습니다")
			await tel_send("💡 미체결 주문을 취소하려면 'ccu' 명령을 사용하세요.")
			return True
		elif command.startswith('stop '):
			parts = command.split()
			if len(parts) == 2:
				# "stop 14", "stop 234" 등 모든 조합 지원
				feature_str = parts[1]
				if feature_str.isdigit():
					return await self.stop(set_auto_start_false=True, feature_numbers=feature_str)
			else:
				await tel_send("❌ 사용법: stop {숫자조합} 또는 stop all (예: stop 14, stop 234, stop all)")
				return False
		elif command == 'report' or command == 'r':
			return await self.report()
		elif command == 'condition' or command == 'cond':
			return await self.condition()
		elif command.startswith('condition ') or command.startswith('cond '):
			parts = command.split()
			if len(parts) >= 2:
				# cond list 또는 cond clear 처리
				if parts[1].lower() == 'list':
					return await self.condition('list')
				elif parts[1].lower() == 'clear':
					return await self.condition('clear')
				else:
					# 여러 번호를 공백으로 구분하여 전달
					numbers = ' '.join(parts[1:])
					return await self.condition(numbers)
			else:
				await tel_send("❌ 사용법: cond {번호들} (예: cond 1 2 4)")
				return False
		elif command == 'help':
			return await self.help()
		elif command.startswith('help '):
			parts = command.split(' ', 1)
			if len(parts) == 2:
				return await self.help(parts[1])
			else:
				return await self.help()
		elif command == 'setting' or command == 'settings':
			return await self.setting()
		elif command.startswith('top '):
			parts = command.split()
			if len(parts) == 2:
				return await self.top(parts[1])
			else:
				await tel_send("❌ 사용법: top {숫자} (예: top 10)")
				return False
		elif command.startswith('tpr '):
			parts = command.split()
			if len(parts) == 2:
				return await self.tpr(parts[1])
			else:
				await tel_send("❌ 사용법: tpr {숫자} (예: tpr 5)")
				return False
		elif command.startswith('slr '):
			parts = command.split()
			if len(parts) == 2:
				return await self.slr(parts[1])
			else:
				await tel_send("❌ 사용법: slr {숫자} (예: slr -10)")
				return False
		elif command.startswith('gapup '):
			parts = command.split()
			if len(parts) == 2:
				return await self.gapup(parts[1])
			else:
				await tel_send("❌ 사용법: gapup {%} (예: gapup 7 → 7% 이상 갭상승 차단)")
				return False
		elif command.startswith('gapdown '):
			parts = command.split()
			if len(parts) == 2:
				return await self.gapdown(parts[1])
			else:
				await tel_send("❌ 사용법: gapdown {%} (예: gapdown 5 → 5% 이상 갭하락 차단)")
				return False
		elif command.startswith('brt '):
			parts = command.split()
			if len(parts) == 2:
				return await self.brt(parts[1])
			else:
				await tel_send("❌ 사용법: brt {숫자} (예: brt 3)")
				return False
		elif command.startswith('bft '):
			parts = command.split()
			if len(parts) == 2:
				return await self.bft(parts[1])
			else:
				await tel_send("❌ 사용법: bft {금액} (예: bft 100000)")
				return False
		elif command.startswith('bftx '):
			parts = command.split()
			if len(parts) == 2:
				return await self.bftx(parts[1])
			else:
				await tel_send("❌ 사용법: bftx {금액} (예: bftx 100000)")
				return False
		elif command.startswith('chart '):
			parts = command.split()
			if len(parts) == 3:
				return await self.chart(parts[1], parts[2])
			else:
				await tel_send("❌ 사용법: chart {x} {y} (예: chart 5 20)")
				return False
		elif command == 'sellall':
			# 백그라운드 태스크로 실행하여 다른 명령어 처리를 블로킹하지 않도록 함
			asyncio.create_task(self.sell_all())
			await tel_send("🔄 전체 매도 작업을 백그라운드에서 시작했습니다...")
			return True
		elif command.startswith('sell '):
			parts = command.split()
			if len(parts) == 2:
				return await self.sell(parts[1].upper())
			else:
				await tel_send("❌ 사용법: sell {종목코드} (예: sell 005930)")
				return False
		elif command.startswith('market '):
			parts = command.split()
			if len(parts) == 3:
				return await self.market(parts[1], parts[2])
			else:
				await tel_send("❌ 사용법: market {시작시간} {종료시간} (예: market 9:00 15:30)")
				return False
		elif command.startswith('btp '):
			parts = command.split()
			if len(parts) == 2:
				return await self.btp(parts[1])
			else:
				await tel_send("❌ 사용법: btp {limit|market|숫자} (예: btp limit, btp market, btp 2)")
				return False
		elif command.startswith('bto '):
			parts = command.split(' ', 1)
			if len(parts) == 2:
				return await self.bto(parts[1])
			else:
				await tel_send("❌ 사용법: bto {시간(초)} [행동] (예: bto 10 cancel, bto 5 market, bto 0)")
				return False
		elif command.startswith('cooldown ') or command.startswith('cd '):
			parts = command.split()
			if len(parts) == 2:
				return await self.cooldown(parts[1])
			else:
				await tel_send("❌ 사용법: cooldown {숫자} 또는 cd {숫자} (예: cooldown 24, cd 0)")
				return False
		elif command.startswith('tsr '):
			parts = command.split()
			if len(parts) in (2, 3):
				return await self.tsr(' '.join(parts[1:]))
			else:
				await tel_send("❌ 사용법: tsr {하락률} [최소수익률] (예: tsr 3, tsr 3 5)")
				return False
		elif command.startswith('mxhold '):
			parts = command.split()
			if len(parts) == 2:
				return await self.maxholdings(parts[1])
			else:
				await tel_send("❌ 사용법: mxhold {숫자} (예: mxhold 10, mxhold 0)")
				return False
		elif command.startswith('block add '):
			parts = command.split(' ', 2)
			if len(parts) == 3:
				return await self.block_add(parts[2])
			else:
				await tel_send("❌ 사용법: block add {패턴} (예: block add 005930, block add 005)")
				return False
		elif command.startswith('block remove '):
			parts = command.split(' ', 2)
			if len(parts) == 3:
				return await self.block_remove(parts[2])
			else:
				await tel_send("❌ 사용법: block remove {패턴} (예: block remove 005930)")
				return False
		elif command == 'block list' or command == 'block':
			return await self.block_list()
		elif command.startswith('brk rate '):
			parts = command.split()
			if len(parts) == 3:
				return await self.brk_rate(parts[2])
			else:
				await tel_send("❌ 사용법: brk rate {숫자} (예: brk rate 3)")
				return False
		elif command.startswith('brk add '):
			parts = command.split(' ', 2)
			if len(parts) == 3:
				return await self.brk_add(parts[2])
			else:
				await tel_send("❌ 사용법: brk add {종목코드} (예: brk add 005930)")
				return False
		elif command.startswith('brk remove '):
			parts = command.split(' ', 2)
			if len(parts) == 3:
				return await self.brk_remove(parts[2])
			else:
				await tel_send("❌ 사용법: brk remove {종목코드} (예: brk remove 005930)")
				return False
		elif command == 'brk list':
			return await self.brk_list()
		elif command.startswith('/ccu') or command.startswith('ccu'):
			# /ccu 명령어 처리
			parts = command.split(' ', 1)
			args = parts[1] if len(parts) > 1 else None
			return await cancel_unfilled_command(self.token_manager, args)
		elif command.startswith('grid add '):
			# grid add 명령어 처리
			parts = command.split()  # grid add 종목코드 단계수 단계별금액차 매수금액
			if len(parts) == 6:  # grid, add, 종목코드, 단계수, 단계별금액차, 매수금액
				args = parts[2:]  # [종목코드, 단계수, 단계별금액차, 매수금액]
				return await process_grid_add(self.script_dir, self.token_manager, args)
			else:
				await tel_send("❌ 사용법: grid add [종목코드] [단계수] [단계별금액차] [매수금액]\n예: grid add 005930 5 1000 100000")
				return False
		elif command.startswith('grid remove '):
			# grid remove 명령어 처리
			parts = command.split(' ', 2)
			if len(parts) == 3:
				args = [parts[2]]  # [종목코드]
				return await process_grid_remove(self.script_dir, args)
			else:
				await tel_send("❌ 사용법: grid remove [종목코드]\n예: grid remove 005930")
				return False
		elif command == 'grid list' or command == 'grid':
			# grid list 명령어 처리
			return await process_grid_list(self.script_dir)
		elif command.startswith('rsv '):
			# rsv 명령어 처리
			parts = command.split(' ', 1)
			args = parts[1] if len(parts) > 1 else None
			return await reserve_command(self.script_dir, self.process_command, args)
		elif command.startswith('wave set '):
			# wave set 명령어 처리
			parts = command.split()
			if len(parts) >= 10:  # wave set buy n n n sell n n n = 10개
				args = parts[2:]  # buy n n n sell n n n = 8개
				return await process_wave_set(self.script_dir, args)
			else:
				await tel_send("❌ 사용법: wave set buy <n> <n> <n> sell <n> <n> <n>\n예: wave set buy 5 10 3 sell 5 10 3")
				return False
		elif command.startswith('wave add '):
			# wave add 명령어 처리
			parts = command.split()
			if len(parts) >= 3:  # wave add 종목코드 총금액 [기준가]
				args = parts[2:]  # 종목코드 총금액 [기준가]
				return await process_wave_add(self.script_dir, self.token_manager, args)
			else:
				await tel_send("❌ 사용법: wave add <종목코드> <총금액> [기준가]\n예: wave add 005930 300000 70000")
				return False
		elif command == 'wave list' or command == 'wave':
			# wave list 명령어 처리
			return await process_wave_list(self.script_dir, self.token_manager)
		elif command.startswith('wave remove '):
			# wave remove 명령어 처리
			parts = command.split()
			if len(parts) == 3:  # wave remove 종목코드 또는 all
				args = [parts[2]]  # 종목코드 또는 all
				return await process_wave_remove(self.script_dir, self.token_manager, args)
			else:
				await tel_send("❌ 사용법: wave remove <종목코드> 또는 wave remove all\n예: wave remove 005930\n예: wave remove all")
				return False
		elif command.startswith('buy '):
			# buy 명령어 처리
			parts = command.split()
			if len(parts) >= 2:
				# parts[1:] : ['005930'] 또는 ['005930', '10'] 등 인자 리스트 전달
				return await buy_command(self.token_manager, parts[1:])
			else:
				await tel_send("❌ 사용법: buy <종목코드> [수량] [가격]")
				return False
		elif command.startswith('srch '):
			# srch 명령어 처리
			parts = command.split()
			if len(parts) == 2:
				return await self.search(parts[1])
			else:
				await tel_send("❌ 사용법: srch {종목코드} (예: srch 005930)")
				return False
		elif command == 'rank':
			# rank 명령어 처리 - 메뉴 출력
			menu_message = "📊 [실시간 시장 순위 조회]\n\n"
			menu_message += "1. 💰 거래대금 상위 (당일 주도주)\n"
			menu_message += "2. 📈 상승률 상위 (급등주)\n"
			menu_message += "3. 📊 거래량 상위 (활발한 거래)\n"
			menu_message += "4. 🔥 인기검색 상위 (투자자 관심)\n\n"
			menu_message += "사용법: rank {번호}\n"
			menu_message += "예: rank 1"
			await tel_send(menu_message)
			return True
		elif command.startswith('rank '):
			# rank {번호} 명령어 처리
			parts = command.split()
			if len(parts) == 2:
				try:
					rank_type = int(parts[1])
					if rank_type < 1 or rank_type > 4:
						await tel_send("❌ 순위 타입은 1~4 사이의 숫자여야 합니다.\n\n사용법: rank {번호}\n예: rank 1")
						return False
					return await self.rank(rank_type)
				except ValueError:
					await tel_send("❌ 순위 타입은 숫자여야 합니다.\n\n사용법: rank {번호}\n예: rank 1")
					return False
			else:
				await tel_send("❌ 사용법: rank {번호} (예: rank 1)")
				return False
		elif command == 'power off':
			# power off 명령어 처리 - 프로그램 완전 종료
			if hasattr(self, 'main_app') and self.main_app:
				await self.main_app.shutdown()
				return True
			else:
				await tel_send("❌ 프로그램 종료에 실패했습니다.")
				return False
		elif command.startswith('fcond '):
			# fcond 명령어 처리
			parts = command.split(' ', 1)
			args = parts[1].split() if len(parts) > 1 else []
			return await fcond_command(self.settings_manager, args, self.websocket)
		else:
			await tel_send(f"❓ 알 수 없는 명령어입니다: {text}")
			return False

