# 지시서: requirements.txt + load_market_change 연결 + Beelink 셋업 (SSH 자동화)

## 전체 범위
1. **Part 1**: `requirements.txt` 생성 (데스크탑)
2. **Part 2**: `load_market_change` 구현 (parquet 방식)
3. **Part 3**: 데스크탑 → GitHub push
4. **Part 4**: SSH로 Beelink 접속하여 clone + 환경 셋업 + 검증 (전부 클로드코드가 직접 수행)

**핵심 원칙: Beelink 작업도 데스크탑 클로드코드가 SSH로 직접 수행. Lee가 Beelink에 직접 명령 입력하는 단계 없음.**

---

# Part 1. requirements.txt 생성

## 1-1. 의존성 분석

데스크탑에서 실행:

```powershell
cd D:\Kiwoom_RtoB

# 모든 .py 파일에서 import 추출
python -c "
import ast, os
from pathlib import Path

imports = set()
for py in Path('.').rglob('*.py'):
    if '.venv' in str(py) or '__pycache__' in str(py):
        continue
    try:
        tree = ast.parse(py.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.add(n.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imports.add(node.module.split('.')[0])
    except Exception as e:
        print(f'skip {py}: {e}')

# 표준 라이브러리 제외
import sys
stdlib = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else set()
external = sorted(imports - stdlib)
for m in external:
    print(m)
" > _imports.txt

cat _imports.txt
```

## 1-2. requirements.txt 작성

**경로:** `D:\Kiwoom_RtoB\requirements.txt`

추출된 외부 패키지 + 봇 운영에 필요한 보조 패키지 정리. 봇 내부 모듈명(sector, tools, openapi_process 등)은 제외.

기본 후보 (코드 분석 결과로 보강):

```
# 데이터 처리
pandas>=2.0
numpy>=1.24
pyarrow>=14.0          # parquet 읽기

# HTTP / WebSocket
httpx>=0.25
websockets>=12.0
requests>=2.31

# 설정 / 직렬화
PyYAML>=6.0
python-dotenv>=1.0

# 스케줄링 (사용 시)
APScheduler>=3.10

# 로깅
loguru>=0.7

# 32-bit OCX/COM (Beelink만, 별도 처리)
# pywin32>=306    # → requirements-beelink.txt로 분리
```

**주의:**
- `pywin32` 같은 Windows 전용 + 32bit 의존성은 `requirements.txt`에 그대로 두면 데스크탑(64bit) 설치 충돌 가능
- 분리 방안:
  - `requirements.txt` — 공통
  - `requirements-beelink.txt` — Beelink 전용 (pywin32 등)
  - 클로드코드가 import 분석 결과 보고 판단

## 1-3. 데스크탑에서 검증

```powershell
cd D:\Kiwoom_RtoB
# 새 가상환경에서 설치 시도
python -m venv .venv-test
.venv-test\Scripts\activate
pip install -r requirements.txt
python -c "from sector.candle_quality import evaluate_candle_quality; print('OK')"
deactivate
rm -r .venv-test  # 검증용 환경 제거
```

설치 실패 패키지 있으면 requirements.txt 수정 후 재시도.

---

# Part 2. load_market_change 구현 (parquet 방식)

## 2-1. 사전 조사

`\\beelink\market_data\bars_1m\index\` 디렉토리 구조 확인. 데스크탑에서:

```powershell
ls \\beelink\market_data\bars_1m\index\
```

다음 정보 확인:
- KOSPI 코드 (보통 `001` 또는 `KS11` 등)
- KOSDAQ 코드 (보통 `101` 또는 `KQ11` 등)
- 파일 경로 패턴 (`{code}/{YYYY}/{YYYYMM}.parquet` 또는 다른 형식)
- 컬럼 스키마 (timestamp, open, high, low, close, volume 등)

확인 결과를 코드에 반영.

## 2-2. data_loaders.py에 load_market_change 구현

**파일:** `kiwoom_RtoB/tools/data_loaders.py`

기존 `load_market_change` NotImplementedError 부분을 다음으로 교체:

```python
import pandas as pd
from pathlib import Path
from datetime import datetime


# 시장지수 코드 (사전 조사 결과 반영)
KOSPI_INDEX_CODE = '001'   # ← 사전 조사로 확정
KOSDAQ_INDEX_CODE = '101'  # ← 사전 조사로 확정

MARKET_DATA_ROOT = Path(r'\\beelink\market_data\bars_1m\index')


def load_market_change(eval_date: str) -> tuple[float, float]:
    """
    eval_date 당일 KOSPI/KOSDAQ 등락률(%) 반환.
    1분봉 parquet에서 첫 시가, 마지막 종가로 계산.
    
    Args:
        eval_date: 'YYYY-MM-DD'
    
    Returns:
        (kospi_chg_pct, kosdaq_chg_pct)
    
    Raises:
        FileNotFoundError: 해당 일자 parquet 없음
        ValueError: 데이터 형식 이상
    """
    kospi_chg = _calc_index_change(KOSPI_INDEX_CODE, eval_date)
    kosdaq_chg = _calc_index_change(KOSDAQ_INDEX_CODE, eval_date)
    return kospi_chg, kosdaq_chg


def _calc_index_change(index_code: str, eval_date: str) -> float:
    """단일 지수의 당일 등락률 계산."""
    dt = datetime.strptime(eval_date, '%Y-%m-%d')
    yyyy = dt.strftime('%Y')
    yyyymm = dt.strftime('%Y%m')
    
    parquet_path = MARKET_DATA_ROOT / index_code / yyyy / f'{yyyymm}.parquet'
    
    if not parquet_path.exists():
        raise FileNotFoundError(f'시장지수 parquet 없음: {parquet_path}')
    
    df = pd.read_parquet(parquet_path)
    
    # 일자 필터 (timestamp 컬럼명/형식은 사전 조사로 확정 후 반영)
    df['date'] = pd.to_datetime(df['timestamp']).dt.strftime('%Y-%m-%d')
    day_df = df[df['date'] == eval_date].sort_values('timestamp')
    
    if len(day_df) == 0:
        raise ValueError(f'{eval_date} 데이터 없음 in {parquet_path}')
    
    open_price = day_df.iloc[0]['open']
    close_price = day_df.iloc[-1]['close']
    
    return (close_price - open_price) / open_price * 100
```

**검증 (데스크탑):**

```powershell
cd D:\Kiwoom_RtoB
.venv\Scripts\activate
python -c "
from tools.data_loaders import load_market_change
k, q = load_market_change('2026-05-15')  # 최근 거래일로
print(f'KOSPI: {k:+.2f}%, KOSDAQ: {q:+.2f}%')
"
```

실제 등락률과 일치하는지 Lee 확인.

---

# Part 3. GitHub push (데스크탑)

```powershell
cd D:\Kiwoom_RtoB
git status
git diff requirements.txt tools/data_loaders.py
git add requirements.txt tools/data_loaders.py
# requirements-beelink.txt 분리했으면 그것도 추가
git commit -m "Add requirements.txt and implement load_market_change (parquet)"
git push origin main  # HTTPS
```

push 성공 확인. 원격 HEAD 일치 확인:

```powershell
git rev-parse HEAD
git ls-remote origin main
```

---

# Part 4. SSH로 Beelink 셋업 (클로드코드 자동 실행)

## 4-1. SSH 연결 정보

- 호스트: `192.168.75.239` (또는 hostname)
- 사용자: Lee 계정 확인 필요
- 키 인증 vs 비번 인증 확인 필요

**Lee에게 사전 확인:**
- SSH 키 셋업 되어 있는지
- Beelink 사용자명
- 작업 디렉토리 권한

확인되면 클로드코드가 다음 명령들을 SSH로 직접 실행.

## 4-2. clone + 환경 셋업 (SSH 명령)

데스크탑 PowerShell에서 클로드코드가 다음을 순차 실행:

```powershell
# 4-2-1. clone (HTTPS — SSH 22 timeout 환경)
ssh USER@192.168.75.239 "cd C:\ && git clone https://github.com/lh4080-alt/Kiwoom_RtoB.git"

# 4-2-2. Python 버전 확인
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && python --version"

# 4-2-3. 가상환경 생성
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && python -m venv .venv"

# 4-2-4. pip 업그레이드 + 패키지 설치
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && .venv\Scripts\python.exe -m pip install --upgrade pip"
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && .venv\Scripts\pip.exe install -r requirements.txt"

# Beelink 전용 패키지 있으면
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && .venv\Scripts\pip.exe install -r requirements-beelink.txt"

# 4-2-5. 데이터 디렉토리 생성
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && mkdir candle_quality_daily, logs 2>NUL"
```

## 4-3. 시크릿 파일 전송 (SCP)

데스크탑에서 클로드코드가 시크릿 파일을 SCP로 전송:

```powershell
# 4-3-1. 시크릿 파일 목록 (.gitignore에 포함된 것들)
$secrets = @(
    "config\real_app_key.txt",
    "config\real_app_secret.txt",
    "config\telegram_token.txt",
    "config\telegram_chat_id.txt"
)

# 4-3-2. 원격 config 디렉토리 생성
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && mkdir config 2>NUL"

# 4-3-3. 각 파일 전송
foreach ($f in $secrets) {
    $local = "D:\Kiwoom_RtoB\$f"
    if (Test-Path $local) {
        scp $local USER@192.168.75.239:/C:/kiwoom_RtoB/$f
    } else {
        Write-Host "SKIP (missing): $f"
    }
}

# (필요시) settings.json 등 추가 파일도 동일 방식
```

**SCP 경로 주의:** Windows 측 경로는 `/C:/...` 형식 또는 `C:\\...` (셸 따라 다름). 첫 시도에서 형식 확인 후 일관 적용.

## 4-4. 검증 (SSH로 원격 실행)

```powershell
# 4-4-1. import 검증
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && .venv\Scripts\python.exe -c 'from sector.candle_quality import evaluate_candle_quality; print(\"candle_quality OK\")'"

# 4-4-2. data_loaders 검증
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && .venv\Scripts\python.exe -c 'from tools.data_loaders import load_market_change; k,q = load_market_change(\"2026-05-15\"); print(f\"KOSPI {k:+.2f}%, KOSDAQ {q:+.2f}%\")'"

# 4-4-3. daily_quality_logger 드라이런
ssh USER@192.168.75.239 "cd C:\kiwoom_RtoB && .venv\Scripts\python.exe tools\daily_quality_logger.py"
```

각 단계 출력 확인. 에러 발생 시 즉시 보고하고 진행 중단.

## 4-5. Task Scheduler 등록

```powershell
# 4-5-1. 관리자 권한 PowerShell로 등록
# (SSH 세션이 관리자 권한이어야 함. 그렇지 않으면 Lee 수동 실행 안내)

ssh USER@192.168.75.239 "powershell -Command \"Start-Process powershell -ArgumentList '-File C:\kiwoom_RtoB\tools\register_daily_logger_task.ps1' -Verb RunAs\""

# 4-5-2. 등록 확인
ssh USER@192.168.75.239 "schtasks /Query /TN KiwoomRtoB_DailyQualityLogger"
```

관리자 권한 SSH 세션 이슈 시: 등록 스크립트 경로를 Lee에게 출력하고 수동 1회 실행 요청.

## 4-6. 최종 보고

다음 항목을 Lee에게 보고:

```
[Beelink 셋업 완료]
- clone: ✅ C:\kiwoom_RtoB
- Python: <버전>
- 가상환경: ✅
- requirements: ✅ <N개 패키지>
- 시크릿 파일: ✅ <N개 전송>
- import 검증: ✅
- load_market_change 검증: KOSPI X.XX%, KOSDAQ X.XX%
- daily_quality_logger 드라이런: <성공/실패>
- Task Scheduler 등록: <성공/Lee 수동 필요>

[다음 거래일 16:30 첫 자동 실행 예정]
```

---

# 주의사항

## SSH 관련
- HTTPS clone 사용 (SSH 22 timeout 환경 확인됨)
- Beelink SSH 접속 정보 사전 확인 필수
- 키 인증 권장 (비번 인증 시 매 명령마다 입력 필요 → 자동화 곤란)
- 키 인증 안 되어 있으면 첫 단계로 키 셋업부터

## Windows SSH 명령 이스케이프
- 따옴표 중첩 주의 (`\"...\"` 형식)
- 경로 백슬래시 (`C:\\path` 또는 `C:/path` 일관 적용)
- 명령이 길거나 복잡하면 .bat/.ps1 파일로 만들어 SCP 전송 후 원격 실행이 안전

## 실패 처리
- 각 SSH 명령 실패 시 즉시 보고하고 진행 중단
- 에러 메시지 그대로 출력 (해석 추가 가능)
- Lee 판단 받고 다음 단계 진행

## 변경 면적
- 데스크탑 신규: `requirements.txt`, (필요시 `requirements-beelink.txt`)
- 데스크탑 수정: `tools/data_loaders.py` (load_market_change 구현)
- Beelink: 전체 clone (신규 디렉토리)
- 원본 봇 코드 무수정 (계속)

---

# 작업 순서 요약

```
1. import 분석 → requirements.txt 작성 (데스크탑)
2. parquet 사전 조사 → load_market_change 구현 (데스크탑)
3. 데스크탑 로컬 검증 (등락률 정상 출력 확인)
4. git commit + HTTPS push
5. SSH 접속 확인 (사용자명, 키 인증)
6. SSH로 Beelink clone + venv + pip install
7. SCP로 시크릿 파일 전송
8. SSH로 원격 검증 (import, load_market_change, dry-run)
9. Task Scheduler 등록 (관리자 권한 이슈 시 Lee 수동)
10. 최종 보고
```
