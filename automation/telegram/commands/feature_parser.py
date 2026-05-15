"""
기능 번호 파싱 유틸리티
"""

def parse_feature_numbers(feature_string):
	"""
	숫자 문자열을 파싱하여 기능 번호 리스트 반환
	예: "14" -> [1, 4], "234" -> [2, 3, 4], "1234" -> [1, 2, 3, 4], "5" -> [5], "12345678" -> [1, 2, 3, 4, 5, 6, 7, 8]
	
	Args:
		feature_string: 기능 번호 문자열 (예: "14", "234", "5", "7", "8")
	
	Returns:
		list: 기능 번호 리스트 (정렬됨)
	"""
	features = []
	for char in feature_string:
		if char.isdigit():
			num = int(char)
			if 1 <= num <= 8 and num not in features:
				features.append(num)
	return sorted(features)  # 정렬하여 일관성 유지

