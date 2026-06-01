"""단일종목 레버리지 ETF 14종 → 기초종목 매핑 (하드코딩).

신규 상장 시 dict에 직접 추가. 패턴 매칭 금지.
※ 본 매핑은 spec v3 §3 확정.
"""

# code → underlying stock_code
ETF_TO_UNDERLYING: dict = {
	# 삼성전자(005930) 단일종목 레버리지 7종
	"491220": "005930",  # KODEX 삼성전자단일종목레버리지 (삼성, 현물+스왑)
	"491410": "005930",  # TIGER 삼성전자단일종목레버리지 (미래에셋, 현물+선물)
	"491820": "005930",  # RISE 삼성전자단일종목레버리지 (KB, 현물혼합)
	"491630": "005930",  # ACE 삼성전자단일종목레버리지 (한투운용, 현물혼합)
	"491550": "005930",  # PLUS 삼성전자단일종목레버리지 (한화, 현물혼합)
	"491950": "005930",  # 1Q 삼성전자선물단일종목레버리지 (하나, 선물중심)
	"491710": "005930",  # KIWOOM 삼성전자선물단일종목레버리지 (키움, 선물중심)
	# SK하이닉스(000660) 단일종목 레버리지 7종
	"491230": "000660",  # KODEX SK하이닉스단일종목레버리지 (삼성, 현물+스왑)
	"491420": "000660",  # TIGER SK하이닉스단일종목레버리지 (미래에셋, 현물+선물)
	"491830": "000660",  # RISE SK하이닉스단일종목레버리지 (KB, 현물혼합)
	"491640": "000660",  # ACE SK하이닉스단일종목레버리지 (한투운용, 현물혼합)
	"491560": "000660",  # PLUS SK하이닉스단일종목레버리지 (한화, 현물혼합)
	"491960": "000660",  # 1Q SK하이닉스선물단일종목레버리지 (하나, 선물중심)
	"491720": "000660",  # KIWOOM SK하이닉스선물단일종목레버리지 (키움, 선물중심)
}

# 기초종목별 역인덱스 (조회 편의)
UNDERLYING_TO_ETFS: dict = {}
for _etf, _under in ETF_TO_UNDERLYING.items():
	UNDERLYING_TO_ETFS.setdefault(_under, []).append(_etf)

# 대상 기초종목 (semi_score 산출 대상)
TARGET_UNDERLYINGS: tuple = ('005930', '000660')

# 기초종목 한글명 (메시지 표시용)
UNDERLYING_NAMES: dict = {
	'005930': '삼성전자',
	'000660': 'SK하이닉스',
}


def get_etfs_for_underlying(stock_code: str) -> list:
	"""기초종목 코드 → 단일ETF 코드 목록."""
	return UNDERLYING_TO_ETFS.get(stock_code, [])


def get_underlying(etf_code: str) -> str:
	"""ETF 코드 → 기초종목 코드. 미등록이면 빈 문자열."""
	return ETF_TO_UNDERLYING.get(etf_code, '')
