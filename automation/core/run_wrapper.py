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
	"""봇 단일 인스턴스 보장 - 중복 가동 차단.

	원인 (6/4 incident): 자동 task `KiwoomRtoB_Bot`이 매일 08:30/부팅 시 발동하는데
	중복 실행 가드가 없어, 이전 봇(ssh -f 잔재 등)이 살아있으면 2개 동시 가동 →
	같은 키움 토큰으로 WebSocket 동시 접속 → 1000 Bye 다발 + 알림 중복 + 매수/매도 race.

	동작 (3단계 polyglot - Beelink PATH에 wmic 없을 수 있어 fallback):
	1. PowerShell Get-CimInstance (cmdline 매칭, 봇만 종료, 안전)
	2. fallback: tasklist로 모든 python.exe PID 추출 후 자기 자신 외 모두 종료
	   (cmdline 확인 못 함 → 운영기에 봇 외 python 없다고 가정)
	3. PID file 갱신 (config/data/bot.pid)
	"""
	import subprocess
	import time
	my_pid = os.getpid()
	_base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
	pid_file = os.path.join(_base, 'config', 'data', 'bot.pid')

	killed = []
	# 시도 1: PowerShell Get-CimInstance (cmdline 매칭 가능 - 안전)
	ps_ok = False
	try:
		r = subprocess.run(
			['powershell', '-NoProfile', '-Command',
			 "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
			 "Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"],
			capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=15,
		)
		ps_ok = r.returncode == 0
		for line in (r.stdout or '').splitlines()[1:]:
			# CSV (Select-Object ProcessId,CommandLine 순서): "pid","cmdline"
			s = line.strip()
			if not s or not s.startswith('"'):
				continue
			# 분리: 첫 ","가 pid/cmdline 경계
			first = s.find('","')
			if first < 0:
				continue
			pid_str = s[1:first]
			cmdline = s[first + 3:].rstrip('"')
			try:
				pid = int(pid_str)
			except ValueError:
				continue
			if pid == my_pid:
				continue
			cmd_lower = cmdline.lower()
			if 'run_wrapper' in cmd_lower or ('core' in cmd_lower and 'main.py' in cmd_lower):
				try:
					subprocess.run(['taskkill', '/F', '/PID', str(pid)],
					               capture_output=True, timeout=5)
					killed.append(pid)
				except Exception:
					pass
	except Exception as e:
		print(f'[startup guard] PowerShell 검사 실패: {e}')

	# 시도 2 (fallback): tasklist로 모든 python.exe → cmdline 검사 못함, 운영기엔 봇만 가정
	# PowerShell이 동작했지만 매칭 못 한 경우엔 추가 안 죽이고 PID file만 갱신 (잔재 보호)
	if not ps_ok:
		try:
			r = subprocess.run(
				['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV', '/NH'],
				capture_output=True, text=True, encoding='cp949', errors='ignore', timeout=10,
			)
			for line in (r.stdout or '').splitlines():
				parts = line.strip().split(',')
				if len(parts) < 2:
					continue
				try:
					pid = int(parts[1].strip('"'))
				except (ValueError, IndexError):
					continue
				if pid == my_pid:
					continue
				try:
					subprocess.run(['taskkill', '/F', '/PID', str(pid)],
					               capture_output=True, timeout=5)
					killed.append(pid)
				except Exception:
					pass
		except Exception as e:
			print(f'[startup guard] tasklist fallback 실패: {e}')

	if killed:
		time.sleep(2)
		print(f'[startup guard] 중복 봇 종료: PID {killed}')
	else:
		print(f'[startup guard] 중복 봇 없음 - my_pid={my_pid}')

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

	# 중복 가동 차단 (Lee 6/4 결정) - 자동 task 중복 발동 + ssh -f 잔재 대비
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

