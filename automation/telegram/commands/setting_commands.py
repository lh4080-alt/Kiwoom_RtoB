import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.tel_send import tel_send
from api.stock_info import fn_ka10001 as stock_info

async def tpr_command(settings_manager, number):
	"""tpr 명령어를 처리합니다 - take_profit_rate 수정"""
	try:
		rate = float(number)
		if settings_manager.update_setting('take_profit_rate', rate):
			await tel_send(f"✅ 익절 기준이 {rate}%로 설정되었습니다")
			return True
		else:
			await tel_send("❌ 익절 기준 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: tpr 5")
		return False
	except Exception as e:
		await tel_send(f"❌ tpr 명령어 실행 중 오류: {e}")
		return False

async def slr_command(settings_manager, number):
	"""slr 명령어를 처리합니다 - stop_loss_rate 수정"""
	try:
		rate = float(number)
		if rate > 0:
			rate = -rate
		if settings_manager.update_setting('stop_loss_rate', rate):
			await tel_send(f"✅ 손절 기준이 {rate}%로 설정되었습니다")
			return True
		else:
			await tel_send("❌ 손절 기준 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: slr -10")
		return False
	except Exception as e:
		await tel_send(f"❌ slr 명령어 실행 중 오류: {e}")
		return False

async def gapup_command(settings_manager, number):
	"""gapup 명령어 — pick 매수 시 갭상승 차단 % (예: gapup 7 → 7% 이상 차단)"""
	try:
		pct = float(number)
		if pct <= 0:
			await tel_send("❌ 갭상승 % 는 0보다 커야 합니다. 예: gapup 7")
			return False
		if settings_manager.update_setting('gap_up', pct):
			await tel_send(f"✅ pick 갭상승 차단이 {pct}% 이상으로 설정되었습니다 (시초가/전일종가 ≥ {1+pct/100:.4f})")
			return True
		else:
			await tel_send("❌ gap_up 설정 실패")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: gapup 7")
		return False
	except Exception as e:
		await tel_send(f"❌ gapup 명령어 실행 중 오류: {e}")
		return False

async def gapdown_command(settings_manager, number):
	"""gapdown 명령어 — pick 매수 시 갭하락 차단 % (예: gapdown 5 → 5% 이상 차단)"""
	try:
		pct = float(number)
		if pct <= 0:
			await tel_send("❌ 갭하락 % 는 0보다 커야 합니다 (양수 입력). 예: gapdown 5")
			return False
		if settings_manager.update_setting('gap_down', pct):
			await tel_send(f"✅ pick 갭하락 차단이 {pct}% 이상으로 설정되었습니다 (시초가/전일종가 ≤ {1-pct/100:.4f})")
			return True
		else:
			await tel_send("❌ gap_down 설정 실패")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: gapdown 5")
		return False
	except Exception as e:
		await tel_send(f"❌ gapdown 명령어 실행 중 오류: {e}")
		return False

async def touch_rate_command(settings_manager, number):
	"""touch_rate 명령어 — touch 매수 반등 임계값 % (예: touch_rate 10)

	트리거 조건: cur >= low + (touch_rate%/100) × (open - low)
	"""
	try:
		pct = float(number)
		if pct <= 0:
			await tel_send("❌ touch_rate % 는 0보다 커야 합니다. 예: touch_rate 10")
			return False
		if settings_manager.update_setting('touch_rate', pct):
			await tel_send(f"✅ touch 반등 임계값이 {pct}%로 설정되었습니다")
			return True
		else:
			await tel_send("❌ touch_rate 설정 실패")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: touch_rate 10")
		return False
	except Exception as e:
		await tel_send(f"❌ touch_rate 명령어 실행 중 오류: {e}")
		return False

async def brt_command(settings_manager, number):
	"""brt 명령어를 처리합니다 - buy_ratio 수정 및 buy_mode를 ratio로 설정"""
	try:
		ratio = float(number)
		# buy_mode를 ratio로 설정
		if not settings_manager.update_setting('buy_mode', 'ratio'):
			await tel_send("❌ 매수 모드 설정에 실패했습니다")
			return False
		if settings_manager.update_setting('buy_ratio', ratio):
			await tel_send(f"✅ 매수 비용 비율이 {ratio}%로 설정되었습니다 (비율 모드)")
			return True
		else:
			await tel_send("❌ 매수 비용 비율 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: brt 3")
		return False
	except Exception as e:
		await tel_send(f"❌ brt 명령어 실행 중 오류: {e}")
		return False

async def bft_command(settings_manager, number):
	"""bft 명령어를 처리합니다 - buy_fixed_amount 수정 및 buy_mode를 fixed로 설정"""
	try:
		amount = float(number)
		if amount <= 0:
			await tel_send("❌ 금액은 0보다 커야 합니다. 예: bft 100000")
			return False
		# buy_mode를 fixed로 설정
		if not settings_manager.update_setting('buy_mode', 'fixed'):
			await tel_send("❌ 매수 모드 설정에 실패했습니다")
			return False
		if settings_manager.update_setting('buy_fixed_amount', amount):
			await tel_send(f"✅ 매수 고정 금액이 {int(amount):,}원으로 설정되었습니다 (고정 금액 모드)")
			return True
		else:
			await tel_send("❌ 매수 고정 금액 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: bft 100000")
		return False
	except Exception as e:
		await tel_send(f"❌ bft 명령어 실행 중 오류: {e}")
		return False

async def bftx_command(settings_manager, number):
	"""bftx 명령어를 처리합니다 - buy_fixed_amount 수정 및 buy_mode를 fixed_strict(엄격)로 설정"""
	try:
		amount = float(number)
		if amount <= 0:
			await tel_send("❌ 금액은 0보다 커야 합니다. 예: bftx 100000")
			return False
		if not settings_manager.update_setting('buy_mode', 'fixed_strict'):
			await tel_send("❌ 매수 모드 설정에 실패했습니다")
			return False
		if settings_manager.update_setting('buy_fixed_amount', amount):
			await tel_send(
				f"✅ 매수 고정 금액이 {int(amount):,}원으로 설정되었습니다 (고정 금액 엄격 모드 · 잔고 부족 시 주문 취소)"
			)
			return True
		else:
			await tel_send("❌ 매수 고정 금액 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: bftx 100000")
		return False
	except Exception as e:
		await tel_send(f"❌ bftx 명령어 실행 중 오류: {e}")
		return False

async def market_command(settings_manager, start_time_str, end_time_str):
	"""market 명령어를 처리합니다 - 장 시작/종료 시간 설정"""
	try:
		# 시작 시간 파싱
		start_parts = start_time_str.split(':')
		if len(start_parts) != 2:
			await tel_send("❌ 시작 시간 형식이 올바르지 않습니다. 예: market 9:00 15:30")
			return False
		
		start_hour = int(start_parts[0])
		start_minute = int(start_parts[1])
		
		if not (0 <= start_hour <= 23) or not (0 <= start_minute <= 59):
			await tel_send("❌ 시작 시간이 올바르지 않습니다. 시는 0-23, 분은 0-59 사이여야 합니다.")
			return False
		
		# 종료 시간 파싱
		end_parts = end_time_str.split(':')
		if len(end_parts) != 2:
			await tel_send("❌ 종료 시간 형식이 올바르지 않습니다. 예: market 9:00 15:30")
			return False
		
		end_hour = int(end_parts[0])
		end_minute = int(end_parts[1])
		
		if not (0 <= end_hour <= 23) or not (0 <= end_minute <= 59):
			await tel_send("❌ 종료 시간이 올바르지 않습니다. 시는 0-23, 분은 0-59 사이여야 합니다.")
			return False
		
		# 마감시간이 시작시간 이후인지 검증
		start_total_minutes = start_hour * 60 + start_minute
		end_total_minutes = end_hour * 60 + end_minute
		
		if end_total_minutes <= start_total_minutes:
			await tel_send("❌ 마감 시간은 시작 시간 이후여야 합니다.")
			return False
		
		# 설정 업데이트
		if (settings_manager.update_setting('market_start_hour', start_hour) and
			settings_manager.update_setting('market_start_minute', start_minute) and
			settings_manager.update_setting('market_end_hour', end_hour) and
			settings_manager.update_setting('market_end_minute', end_minute)):
			await tel_send(f"✅ 장 시간이 {start_hour:02d}:{start_minute:02d} ~ {end_hour:02d}:{end_minute:02d}로 설정되었습니다")
			return True
		else:
			await tel_send("❌ 장 시간 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 시간 형식입니다. 예: market 9:00 15:30")
		return False
	except Exception as e:
		await tel_send(f"❌ market 명령어 실행 중 오류: {e}")
		return False

async def btp_command(settings_manager, order_type):
	"""btp 명령어를 처리합니다 - buy_order_type 수정 (limit: 보통가, market: 시장가, 또는 정수: 호가 조정)"""
	try:
		order_type_str = str(order_type).strip()
		order_type_lower = order_type_str.lower()
		
		if order_type_lower == 'limit':
			if settings_manager.update_setting('buy_order_type', 'limit'):
				await tel_send("✅ 매수 주문 타입이 보통가(지정가)로 설정되었습니다")
				return True
			else:
				await tel_send("❌ 매수 주문 타입 설정에 실패했습니다")
				return False
		elif order_type_lower == 'market':
			if settings_manager.update_setting('buy_order_type', 'market'):
				await tel_send("✅ 매수 주문 타입이 시장가로 설정되었습니다")
				return True
			else:
				await tel_send("❌ 매수 주문 타입 설정에 실패했습니다")
				return False
		else:
			# 정수 입력인지 확인 (양수: 호가 낮춤, 음수: 호가 높임, 0: 현재가)
			try:
				ticks = int(order_type_str)
				# 정수 값을 문자열로 저장 (로직에서 정수로 파싱하여 사용)
				if settings_manager.update_setting('buy_order_type', str(ticks)):
					if ticks > 0:
						await tel_send(f"✅ 매수 주문이 {ticks}호가 낮춤으로 설정되었습니다 (지정가)")
					elif ticks < 0:
						await tel_send(f"✅ 매수 주문이 {abs(ticks)}호가 높임으로 설정되었습니다 (지정가)")
					else:
						await tel_send("✅ 매수 주문이 현재가로 설정되었습니다 (지정가)")
					return True
				else:
					await tel_send("❌ 매수 주문 타입 설정에 실패했습니다")
					return False
			except ValueError:
				await tel_send("❌ 잘못된 주문 타입입니다. 'limit', 'market', 또는 정수를 입력해주세요. (예: btp limit, btp market, btp 2, btp -2)")
				return False
	except Exception as e:
		await tel_send(f"❌ btp 명령어 실행 중 오류: {e}")
		return False

async def cooldown_command(settings_manager, number):
	"""cooldown 명령어를 처리합니다 - sell_cooldown_hours 수정"""
	try:
		hours = float(number)
		if hours < 0:
			await tel_send("❌ 쿨다운 시간은 0 이상이어야 합니다. 예: cooldown 24 또는 cooldown 0.5")
			return False
		
		if settings_manager.update_setting('sell_cooldown_hours', hours):
			if hours == 0:
				await tel_send("✅ 매도 후 재매수 쿨다운이 비활성화되었습니다")
			else:
				# 시간과 분으로 변환
				whole_hours = int(hours)
				minutes = int((hours - whole_hours) * 60)
				
				# 메시지 포맷팅
				if whole_hours > 0 and minutes > 0:
					time_str = f"{whole_hours}시간 {minutes}분"
				elif whole_hours > 0:
					time_str = f"{whole_hours}시간"
				elif minutes > 0:
					time_str = f"{minutes}분"
				else:
					time_str = "0분"
				
				await tel_send(f"✅ 매도 후 재매수 쿨다운이 {time_str}으로 설정되었습니다")
			return True
		else:
			await tel_send("❌ 쿨다운 시간 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: cooldown 24 또는 cooldown 0.5")
		return False
	except Exception as e:
		await tel_send(f"❌ cooldown 명령어 실행 중 오류: {e}")
		return False

async def tsr_command(settings_manager, args_str):
	"""tsr 명령어를 처리합니다 - trailing_stop_rate / trailing_min_profit 수정
	
	사용법:
	- tsr {하락률}
	- tsr {하락률} {최소수익률}
	예) tsr 3 5  -> 5% 수익 도달 후, 고점 대비 3% 하락 시 매도
	"""
	try:
		parts = str(args_str).strip().split()
		if len(parts) == 0:
			await tel_send("❌ 사용법: tsr {하락률} [최소수익률] (예: tsr 3, tsr 3 5)")
			return False
		if len(parts) > 2:
			await tel_send("❌ 사용법: tsr {하락률} [최소수익률] (예: tsr 3, tsr 3 5)")
			return False
		
		try:
			trailing_stop_rate = float(parts[0])
			trailing_min_profit = float(parts[1]) if len(parts) == 2 else 0.0
		except ValueError:
			await tel_send("❌ 잘못된 숫자 형식입니다. 예: tsr 3, tsr 3 5")
			return False
		
		if trailing_stop_rate < 0:
			await tel_send("❌ 트레일링 스탑 하락률은 0 이상이어야 합니다. 예: tsr 3")
			return False
		
		if trailing_min_profit < 0:
			await tel_send("❌ 최소 발동 수익률은 0 이상이어야 합니다. 예: tsr 3 5")
			return False
		
		ok = (
			settings_manager.update_setting('trailing_stop_rate', trailing_stop_rate) and
			settings_manager.update_setting('trailing_min_profit', trailing_min_profit)
		)
		if ok:
			await tel_send(
				f"✅ 트레일링 스탑 설정 완료\n"
				f"  - 고점 대비 하락률: {trailing_stop_rate}%\n"
				f"  - 최소 발동 수익률: {trailing_min_profit}%\n\n"
				f"💡 예시: tsr 3 5  (수익률 5% 도달 후, 고점 대비 3% 하락 시 매도)"
			)
			return True
		
		await tel_send("❌ 트레일링 스탑 설정에 실패했습니다")
		return False
	except Exception as e:
		await tel_send(f"❌ tsr 명령어 실행 중 오류: {e}")
		return False

async def maxholdings_command(settings_manager, number):
	"""mxhold 명령어를 처리합니다 - max_holdings 수정"""
	try:
		max_holdings = int(float(number))  # 소수점 입력도 정수로 변환
		if max_holdings < 0:
			await tel_send("❌ 보유종목 개수 제한은 0 이상이어야 합니다. 예: mxhold 10")
			return False
		
		if settings_manager.update_setting('max_holdings', max_holdings):
			if max_holdings == 0:
				await tel_send("✅ 보유종목 개수 제한이 비활성화되었습니다 (제한 없음)")
			else:
				await tel_send(f"✅ 보유종목 개수 제한이 {max_holdings}개로 설정되었습니다")
			return True
		else:
			await tel_send("❌ 보유종목 개수 제한 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: mxhold 10")
		return False
	except Exception as e:
		await tel_send(f"❌ mxhold 명령어 실행 중 오류: {e}")
		return False

async def block_add_command(settings_manager, pattern):
	"""block add 명령어를 처리합니다 - 자동매매 금지 목록에 패턴 추가"""
	try:
		if not pattern or not pattern.strip():
			await tel_send("❌ 패턴을 입력해주세요. 예: block add 005930")
			return False
		
		pattern = pattern.strip()
		
		# 현재 금지 목록 가져오기
		blocklist = settings_manager.get_setting('auto_sell_blocklist', [])
		if not isinstance(blocklist, list):
			blocklist = []
		
		# 이미 존재하는지 확인
		if pattern in blocklist:
			await tel_send(f"⚠️ '{pattern}'는 이미 금지 목록에 있습니다.")
			return False
		
		# 목록에 추가
		blocklist.append(pattern)
		if settings_manager.update_setting('auto_sell_blocklist', blocklist):
			await tel_send(f"✅ '{pattern}'가 자동매매 금지 목록에 추가되었습니다.")
			return True
		else:
			await tel_send("❌ 금지 목록 추가에 실패했습니다.")
			return False
	except Exception as e:
		await tel_send(f"❌ block add 명령어 실행 중 오류: {e}")
		return False

async def block_remove_command(settings_manager, pattern):
	"""block remove 명령어를 처리합니다 - 자동매매 금지 목록에서 패턴 제거"""
	try:
		if not pattern or not pattern.strip():
			await tel_send("❌ 패턴을 입력해주세요. 예: block remove 005930")
			return False
		
		pattern = pattern.strip()
		
		# 현재 금지 목록 가져오기
		blocklist = settings_manager.get_setting('auto_sell_blocklist', [])
		if not isinstance(blocklist, list):
			blocklist = []
		
		# 목록에서 제거
		if pattern not in blocklist:
			await tel_send(f"⚠️ '{pattern}'는 금지 목록에 없습니다.")
			return False
		
		blocklist.remove(pattern)
		if settings_manager.update_setting('auto_sell_blocklist', blocklist):
			await tel_send(f"✅ '{pattern}'가 자동매매 금지 목록에서 제거되었습니다.")
			return True
		else:
			await tel_send("❌ 금지 목록 제거에 실패했습니다.")
			return False
	except Exception as e:
		await tel_send(f"❌ block remove 명령어 실행 중 오류: {e}")
		return False

async def block_list_command(settings_manager):
	"""block list 명령어를 처리합니다 - 현재 자동매매 금지 목록 조회"""
	try:
		blocklist = settings_manager.get_setting('auto_sell_blocklist', [])
		if not isinstance(blocklist, list):
			blocklist = []
		
		if len(blocklist) == 0:
			await tel_send("📋 자동매매 금지 목록이 비어있습니다.")
			return True
		
		# 목록 포맷팅
		message = "📋 [자동매매 금지 목록]\n\n"
		for i, pattern in enumerate(blocklist, 1):
			message += f"{i}. {pattern}\n"
		
		await tel_send(message)
		return True
	except Exception as e:
		await tel_send(f"❌ block list 명령어 실행 중 오류: {e}")
		return False

async def brk_rate_command(settings_manager, number):
	"""brk rate 명령어를 처리합니다 - 돌파율 기준 설정"""
	try:
		rate = float(number)
		if rate <= 0:
			await tel_send("❌ 돌파율은 0보다 커야 합니다. 예: brk rate 3")
			return False
		
		if settings_manager.update_setting('break_rate', rate):
			await tel_send(f"✅ 돌파율이 {rate}%로 설정되었습니다")
			return True
		else:
			await tel_send("❌ 돌파율 설정에 실패했습니다")
			return False
	except ValueError:
		await tel_send("❌ 잘못된 숫자 형식입니다. 예: brk rate 3")
		return False
	except Exception as e:
		await tel_send(f"❌ brk rate 명령어 실행 중 오류: {e}")
		return False

async def brk_add_command(settings_manager, pattern):
	"""brk add 명령어를 처리합니다 - 돌파 감시 목록에 종목 추가"""
	try:
		if not pattern or not pattern.strip():
			await tel_send("❌ 종목 코드를 입력해주세요. 예: brk add 005930")
			return False
		
		pattern = pattern.strip()
		break_list = settings_manager.get_setting('break_stock_list', [])
		if not isinstance(break_list, list):
			break_list = []
		
		if pattern in break_list:
			await tel_send(f"⚠️ '{pattern}'는 이미 돌파 감시 목록에 있습니다.")
			return False
		
		break_list.append(pattern)
		if settings_manager.update_setting('break_stock_list', break_list):
			await tel_send(f"✅ '{pattern}'가 돌파 감시 목록에 추가되었습니다.")
			return True
		else:
			await tel_send("❌ 돌파 감시 목록 추가에 실패했습니다.")
			return False
	except Exception as e:
		await tel_send(f"❌ brk add 명령어 실행 중 오류: {e}")
		return False

async def brk_remove_command(settings_manager, pattern):
	"""brk remove 명령어를 처리합니다 - 돌파 감시 목록에서 종목 제거"""
	try:
		if not pattern or not pattern.strip():
			await tel_send("❌ 종목 코드를 입력해주세요. 예: brk remove 005930")
			return False
		
		pattern = pattern.strip()
		break_list = settings_manager.get_setting('break_stock_list', [])
		if not isinstance(break_list, list):
			break_list = []
		
		if pattern not in break_list:
			await tel_send(f"⚠️ '{pattern}'는 돌파 감시 목록에 없습니다.")
			return False
		
		break_list.remove(pattern)
		if settings_manager.update_setting('break_stock_list', break_list):
			await tel_send(f"✅ '{pattern}'가 돌파 감시 목록에서 제거되었습니다.")
			return True
		else:
			await tel_send("❌ 돌파 감시 목록 제거에 실패했습니다.")
			return False
	except Exception as e:
		await tel_send(f"❌ brk remove 명령어 실행 중 오류: {e}")
		return False

async def brk_list_command(settings_manager, token_manager):
	"""brk list 명령어를 처리합니다 - 돌파 감시 목록 조회"""
	try:
		break_list = settings_manager.get_setting('break_stock_list', [])
		if not isinstance(break_list, list):
			break_list = []
		
		if len(break_list) == 0:
			await tel_send("📋 돌파 감시 목록이 비어있습니다.")
			return True
		
		# 토큰이 없으면 새로 발급
		token = token_manager.token or await token_manager.get_token()
		
		lines = ["📋 [돌파 감시 목록]"]
		for idx, code in enumerate(break_list, 1):
			try:
				info = await stock_info(code, token=token)
				name = info.get('stk_nm', code) if isinstance(info, dict) else code
			except Exception:
				name = code
			lines.append(f"{idx}. {code} {name}")
		
		await tel_send("\n".join(lines))
		return True
	except Exception as e:
		await tel_send(f"❌ brk list 명령어 실행 중 오류: {e}")
		return False

async def bto_command(settings_manager, args_str):
	"""bto 명령어를 처리합니다 - buy_timeout 및 buy_timeout_action 설정
	
	사용법:
	- bto {시간(초)} [행동]
	- bto 10 cancel  -> 10초 후 미체결 시 취소
	- bto 5 market    -> 5초 후 미체결 시 시장가로 전환
	- bto 0          -> 타임아웃 기능 Off
	"""
	try:
		parts = str(args_str).strip().split()
		if len(parts) == 0:
			await tel_send("❌ 사용법: bto {시간(초)} [행동] (예: bto 10 cancel, bto 5 market, bto 0)")
			return False
		if len(parts) > 2:
			await tel_send("❌ 사용법: bto {시간(초)} [행동] (예: bto 10 cancel, bto 5 market, bto 0)")
			return False
		
		try:
			timeout_seconds = int(float(parts[0]))
			action = parts[1].lower() if len(parts) == 2 else 'cancel'
		except ValueError:
			await tel_send("❌ 잘못된 숫자 형식입니다. 예: bto 10 cancel")
			return False
		
		if timeout_seconds < 0:
			await tel_send("❌ 타임아웃 시간은 0 이상이어야 합니다. 예: bto 10")
			return False
		
		if action not in ['cancel', 'market']:
			await tel_send("❌ 행동은 'cancel' 또는 'market'이어야 합니다. 예: bto 10 cancel")
			return False
		
		ok = (
			settings_manager.update_setting('buy_timeout', timeout_seconds) and
			settings_manager.update_setting('buy_timeout_action', action)
		)
		
		if ok:
			if timeout_seconds == 0:
				await tel_send("✅ 매수 주문 타임아웃이 비활성화되었습니다")
			else:
				action_kr = "취소" if action == 'cancel' else "시장가 전환"
				await tel_send(
					f"✅ 매수 주문 타임아웃 설정 완료\n"
					f"  - 타임아웃 시간: {timeout_seconds}초\n"
					f"  - 미체결 시 행동: {action_kr}\n\n"
					f"💡 예시: bto 10 cancel  (10초 후 미체결 시 취소)"
				)
			return True
		
		await tel_send("❌ 매수 주문 타임아웃 설정에 실패했습니다")
		return False
	except Exception as e:
		await tel_send(f"❌ bto 명령어 실행 중 오류: {e}")
		return False

