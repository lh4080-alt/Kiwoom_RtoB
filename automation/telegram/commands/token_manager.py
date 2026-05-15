import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.login import fn_au10001

class TokenManager:
	"""토큰 관리를 담당하는 클래스"""
	
	def __init__(self):
		self.token = None  # 현재 사용 중인 토큰
	
	def reset_token(self):
		"""기존 토큰을 초기화합니다. 모드 전환 시 사용됩니다."""
		self.token = None
		print("토큰이 초기화되었습니다.")
	
	async def get_token(self, force_refresh=False):
		"""토큰을 가져옵니다. 기존 토큰이 있으면 재사용하고, 없을 때만 새로 발급합니다."""
		# 강제 갱신이거나 기존 토큰이 없으면 새로 발급
		if force_refresh or not self.token:
			try:
				token = await fn_au10001()
				if token:
					old_token = self.token[:10] + "..." if self.token else "없음"
					self.token = token
					if force_refresh:
						print(f"강제 토큰 갱신 완료: {old_token} -> {token[:10]}...")
					else:
						print(f"새로운 토큰 발급 완료: {token[:10]}...")
					return token
				else:
					print("토큰 발급 실패")
					return None
			except Exception as e:
				# e가 비어 보이는 경우가 있어 타입/repr까지 출력
				print(f"토큰 발급 중 오류: {type(e).__name__}: {e!r}")
				return None
		
		# 기존 토큰 반환
		return self.token

