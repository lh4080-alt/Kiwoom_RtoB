"""
SSL 인증서 검증을 비활성화하는 wrapper 스크립트
Azure Windows Server 2019에서 self-signed certificate 오류를 해결하기 위해 사용
"""
import ssl
import os

# SSL 컨텍스트를 생성하여 인증서 검증 비활성화
# websockets 라이브러리가 사용하는 기본 SSL 컨텍스트를 수정
_original_create_default_context = ssl.create_default_context

def create_unverified_ssl_context(*args, **kwargs):
	"""인증서 검증을 비활성화한 SSL 컨텍스트를 생성"""
	context = _original_create_default_context(*args, **kwargs)
	context.check_hostname = False
	context.verify_mode = ssl.CERT_NONE
	return context

# 기본 SSL 컨텍스트 생성 함수를 교체
ssl._create_default_https_context = ssl._create_unverified_context
ssl.create_default_context = create_unverified_ssl_context

# main.py를 실행
if __name__ == '__main__':
	import runpy
	import os
	import sys

	# 봇 logging 설정 (KST 시각 + httpx/httpcore 음소거)
	sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
	from core.logging_config import setup_logging
	setup_logging()

	# 현재 스크립트의 디렉토리로 이동
	script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	os.chdir(script_dir)

	# main.py를 실행 (runpy를 사용하여 __main__ 모듈로 실행)
	runpy.run_path('core/main.py', run_name='__main__')

