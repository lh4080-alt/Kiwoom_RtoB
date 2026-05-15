"""
종목코드 정규화 유틸리티
키움 REST API의 종목코드 전처리 로직

키움 API는 잔고 조회 응답 등에서 종목코드 앞에 'A' 접두어를 붙여서 반환하는 경우가 있습니다.
하지만 종목코드 자체에 'A'가 포함될 수 있으므로 (예: 0001A0, J0036221D),
첫 번째 글자만 확인하여 조건부로 제거해야 합니다.

규칙:
- 첫 번째 글자가 'A'이고 전체 길이가 7자리 이상인 경우에만 맨 앞의 'A'를 제거
- 그 외의 경우는 문자열을 있는 그대로 유지
"""

def normalize_stock_code(stk_cd):
	"""
	종목코드를 정규화합니다.
	
	키움 API는 잔고 조회 응답 등에서 종목코드 앞에 'A' 접두어를 붙여서 반환하는 경우가 있습니다.
	(예: 삼성전자 005930을 A005930으로 반환)
	
	하지만 종목코드 자체에 'A'가 포함될 수 있으므로 (예: 0001A0, J0036221D),
	첫 번째 글자만 확인하여 조건부로 제거해야 합니다.
	
	Args:
		stk_cd: 종목코드 (문자열, None 가능)
	
	Returns:
		str: 정규화된 종목코드
		
	Examples:
		>>> normalize_stock_code('A005930')
		'005930'
		>>> normalize_stock_code('0001A0')
		'0001A0'
		>>> normalize_stock_code('J0036221D')
		'J0036221D'
		>>> normalize_stock_code('005930')
		'005930'
	"""
	if not stk_cd:
		return ''
	
	# 문자열로 변환 및 공백 제거
	stk_cd_str = str(stk_cd).strip()
	
	if not stk_cd_str:
		return ''
	
	# 첫 번째 글자가 'A'이고 전체 길이가 7자리 이상인 경우에만 맨 앞의 'A' 제거
	# (키움이 붙이는 관리용 접두어는 보통 6자리 종목코드 앞에 붙어서 7자리가 됨)
	if len(stk_cd_str) >= 7 and stk_cd_str[0] == 'A':
		return stk_cd_str[1:]
	
	# 그 외의 경우는 있는 그대로 반환
	return stk_cd_str
