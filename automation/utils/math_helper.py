"""
호가 계산 유틸리티 함수
한국 주식 시장의 호가 단위(Tick Size) 계산 및 가격 조정
"""

def get_tick_size(price, market_type='KOSPI'):
	"""
	가격대별 호가 단위 계산
	
	Args:
		price: 현재 가격
		market_type: 시장 구분 ('KOSPI' or 'KOSDAQ', 기본값: 'KOSPI')
	
	Returns:
		int: 호가 단위 (원)
	
	참고:
		ETF/ELW 등은 5원 단위이나, 편의상 KOSPI/KOSDAQ 주식 기준으로 통일
	"""
	if price < 2000:
		return 1
	elif price < 5000:
		return 5
	elif price < 20000:
		return 10
	elif price < 50000:
		return 50
	elif price < 200000:
		return 100
	elif price < 500000:
		return 500
	else:
		return 1000

def calculate_lower_price(current_price, ticks):
	"""
	현재가에서 ticks만큼 낮은 가격 계산
	
	Args:
		current_price: 현재 가격
		ticks: 낮출 호가 개수 (정수)
	
	Returns:
		float: 계산된 가격
	"""
	if ticks <= 0:
		return current_price
	
	target_price = current_price
	for _ in range(ticks):
		tick = get_tick_size(target_price)
		target_price -= tick
	
	# 가격이 0보다 작아지지 않도록 보정
	if target_price < 0:
		target_price = 0
	
	return target_price

def calculate_price_by_btp(current_price, btp):
	"""
	BTP(Bid Tick Price) 기반 목표 가격 계산
	
	BTP > 0: 가격을 낮춤 (싸게 정정) → 현재가 - (BTP * 호가단위)
	BTP < 0: 가격을 높임 (비싸게 정정, 즉시 체결 유도 등) → 현재가 + (abs(BTP) * 호가단위)
	BTP = 0: 현재가 유지
	
	Args:
		current_price: 현재 가격
		btp: Bid Tick Price (정수, 양수면 낮춤, 음수면 높임)
	
	Returns:
		float: 계산된 목표 가격
	
	예시:
		현재가 50000원, BTP = -3 → 50150원 (3틱 높임)
		현재가 50000원, BTP = 2 → 49900원 (2틱 낮춤)
	"""
	if btp == 0:
		return current_price
	
	target_price = current_price
	
	if btp > 0:
		# 가격을 낮춤 (BTP만큼 틱 낮춤)
		for _ in range(btp):
			tick = get_tick_size(target_price)
			target_price -= tick
			# 가격이 0보다 작아지지 않도록 보정
			if target_price < 0:
				target_price = 0
				break
	else:
		# 가격을 높임 (abs(BTP)만큼 틱 높임)
		for _ in range(abs(btp)):
			tick = get_tick_size(target_price)
			target_price += tick
	
	return target_price

