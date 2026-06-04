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

def _enforce_single_instance():
	"""봇 단일 인스턴스 보장 — 중복 가동 차단.

	원인 (6/4 incident): 자동 task `KiwoomRtoB_Bot`이 매일 08:30/부팅 시 발동하는데
	중복 실행 가드가 없어, 이전 봇(ssh -f 잔재 등)이 살아있으면 2개 동시 가동 →
	같은 키움 토큰으로 WebSocket 동시 접속 → 1000 Bye 다발 + 알림 중복 + 매수/매도 race.

	동작:
	1. wmic로 run_wrapper/main.py 가동 중인 python.exe 검사
	2. 자기 자신(os.getpid()) 제외하고 모두 종료
	3. PID file 갱신 (config/data/bot.pid)
	"""
	import subprocess
	import time
	my_pid = os.getpid()
	_base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
	pid_file = os.path.join(_base, 'config', 'data', 'bot.pid')

	killed = []
	try:
		r = subprocess.run(
			['wmic', 'process', 'where', "name='python.exe'", 'get', 'ProcessId,CommandLine', '/FORMAT:CSV'],
			capture_output=True, text=True, encoding='cp949', errors='ignore', timeout=10,
		)
		for line in (r.stdout or '').splitlines():
			parts = line.split(',')
			if len(parts) < 3:
				continue
			cmdline = parts[1] if len(parts) >= 2 else ''
			try:
				pid = int(parts[-1].strip())
			except (ValueError, IndexError):
				continue
			if pid == my_pid:
				continue
			cmd_lower = cmdline.lower()
			# run_wrapper.py 또는 core/main.py 가동 중인 봇만 종료 (다른 python 작업 보호)
			if 'run_wrapper' in cmd_lower or ('core' in cmd_lower and 'main.py' in cmd_lower):
				try:
					subprocess.run(['taskkill', '/F', '/PID', str(pid)],
					               capture_output=True, timeout=5)
					killed.append(pid)
				except Exception:
					pass
	except Exception as e:
		print(f'[startup guard] wmic 검사 실패 (계속): {e}')

	if killed:
		time.sleep(2)
		print(f'[startup guard] 중복 봇 종료: PID {killed}')

	try:
		os.makedirs(os.path.dirname(pid_file), exist_ok=True)
		with open(pid_file, 'w') as f:
			f.write(str(my_pid))
		print(f'[startup guard] PID file 갱신: {my_pid}')
	except Exception as e:
		print(f'[startup guard] PID file 쓰기 실패 (계속): {e}')


# main.py를 실행
if __name__ == '__main__':
	import runpy
	import os
	import sys

	# 중복 가동 차단 (Lee 6/4 결정) — 자동 task 중복 발동 + ssh -f 잔재 대비
	_enforce_single_instance()

	# 봇 logging 설정 (KST 시각 + httpx/httpcore 음소거)
	# sys.path: automation/ (기존 봇 import용) + 프로젝트 루트 (신규 sector/ 모듈 import용)
	_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
	_AUTOMATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	sys.path.insert(0, _AUTOMATION)
	sys.path.insert(0, _ROOT)
	from core.logging_config import setup_logging
	setup_logging()

	# 현재 스크립트의 디렉토리로 이동
	script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	os.chdir(script_dir)

	# main.py를 실행 (runpy를 사용하여 __main__ 모듈로 실행)
	runpy.run_path('core/main.py', run_name='__main__')

