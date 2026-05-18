# 지시서: Task Scheduler 등록 + 봇 실행 위치 확인/이전

## 범위
1. **Part 1**: 봇 실행 위치 현황 진단 (어디서 collection_pool.json에 쓰는가)
2. **Part 2**: PC에서 돌고 있으면 → Beelink로 봇 본체 이전
3. **Part 3**: daily_quality_logger Task Scheduler 등록
4. **Part 4**: 최종 동작 검증

**Lee 확인 단계 최소화. 클로드코드가 SSH로 Beelink 진단·실행 전부 수행. Lee 개입은 관리자 권한 PowerShell 1회만 (4-3 단계).**

---

# Part 1. 봇 실행 위치 진단

## 1-1. 현재 가동 중인 프로세스 확인

**데스크탑 (PC):**
```powershell
# PC에서 Python 프로세스 + kiwoom_RtoB 관련 확인
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, CommandLine
```

**Beelink (SSH):**
```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-WmiObject Win32_Process -Filter 'Name=''python.exe''' | Select-Object ProcessId, CommandLine | Format-List\""
```

각각의 출력에서 `kiwoom_RtoB` 경로를 포함한 python 프로세스가 있는지 확인.

## 1-2. Task Scheduler 등록된 봇 작업 확인

**PC:**
```powershell
schtasks /Query /FO LIST | Select-String -Pattern "kiwoom|RtoB|봇" -Context 0,3
```

**Beelink (SSH):**
```powershell
ssh beelink\lh408@192.168.75.239 "schtasks /Query /FO LIST | findstr /I \"kiwoom RtoB 봇\""
```

## 1-3. collection_pool.json 최근 수정 시간 확인

봇이 어디서 수집풀에 쓰고 있는지 mtime으로 확인.

**PC:**
```powershell
Get-Item D:\Kiwoom_RtoB\config\data\collection_pool.json -ErrorAction SilentlyContinue | Select-Object FullName, Length, LastWriteTime
```

**Beelink (SSH):**
```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-Item C:\Kiwoom_RtoB\config\data\collection_pool.json -ErrorAction SilentlyContinue | Select-Object FullName, Length, LastWriteTime\""
```

## 1-4. 진단 결과 분류

| 케이스 | 판정 | 다음 단계 |
|--------|------|----------|
| PC에 python 프로세스 + collection_pool.json mtime 최근 | PC 운영 중 | **Part 2 (Beelink 이전) 실행** |
| Beelink에 python 프로세스 + collection_pool.json mtime 최근 | Beelink 운영 중 | **Part 2 스킵, Part 3로 직행** |
| 양쪽 다 프로세스 없음 + mtime 오래됨 | 봇 미가동 | **Part 2 스킵, Part 3 후 봇 가동은 별도 작업** |
| 양쪽 다 프로세스 있음 | ⚠️ 중복 가동 | **즉시 보고, 진행 중단** |

각 케이스에 따라 Part 2 실행 여부 결정.

---

# Part 2. PC → Beelink 봇 본체 이전 (Part 1에서 "PC 운영 중"인 경우만)

## 2-1. PC 봇 중단

PC에서 실행 중인 봇 프로세스 식별 후 종료:

```powershell
# 1-1에서 확인된 PID로
Stop-Process -Id <PID> -Force

# Task Scheduler 등록된 거면
schtasks /Change /TN <작업명> /DISABLE
```

PC 봇이 어떻게 가동되었는지(Task Scheduler/수동 실행/배치파일/IDE 디버그) Part 1 결과에 따라 적합한 중단 방법 선택. 불명확하면 보고하고 Lee 판단 받음.

## 2-2. PC → Beelink 데이터 이전

```powershell
# 핵심 데이터 디렉토리 전체 동기화 (scp -r)
# 시크릿은 이미 옮겨져 있으므로 데이터만

# config/data/ (collection_pool.json 등)
scp -r D:\Kiwoom_RtoB\config\data\* beelink\lh408@192.168.75.239:/C:/Kiwoom_RtoB/config/data/

# bar_storage/ (존재 시)
if (Test-Path D:\Kiwoom_RtoB\bar_storage) {
    scp -r D:\Kiwoom_RtoB\bar_storage\* beelink\lh408@192.168.75.239:/C:/Kiwoom_RtoB/bar_storage/
}

# waiting_pool.db (존재 시)
if (Test-Path D:\Kiwoom_RtoB\waiting_pool.db) {
    scp D:\Kiwoom_RtoB\waiting_pool.db beelink\lh408@192.168.75.239:/C:/Kiwoom_RtoB/
}

# trades.db, logs/ 등 추가 데이터 디렉토리도 동일 방식
```

각 파일 전송 후 mtime/크기 비교로 일치 확인:
```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-ChildItem C:\Kiwoom_RtoB\config\data\ | Select-Object Name, Length, LastWriteTime\""
```

## 2-3. Beelink에서 봇 본체 실행 방식 결정

봇이 어떻게 가동되어야 하는지에 따라 분기:

**(a) 봇이 상시 가동(데몬형)이면:**
```powershell
# Task Scheduler에 startup 작업 등록
# 또는 nssm/winsw로 Windows Service 등록
# 봇 메인 스크립트 경로/실행 방식은 PC 운영 방식 그대로 복제
```

**(b) 봇이 시간 트리거(09:00 시작/15:30 종료 등)이면:**
```powershell
# Task Scheduler에 시간 트리거 작업 등록
```

PC에서 어떻게 돌았는지 Part 1-1, 1-2 결과를 기반으로 동일 방식 재현.

**이 부분은 봇 본체 구조에 따라 다르므로, Part 1 결과 보고 후 구체 단계 확정. 일단 Part 2-3은 보류하고 Part 3 진행 가능.**

## 2-4. PC 봇 코드 정리

PC `D:\Kiwoom_RtoB`는 dev용으로 유지 (코드 수정 + git push 용). 데이터 디렉토리는 더 이상 쓰지 않음 — 단, 삭제는 하지 말고 그대로 둠 (혹시 모를 백업).

PC에서 봇 실행 시도 방지를 위해 README에 명시 권장 (선택):

```markdown
# kiwoom_RtoB

## 운영 환경
- 코드: PC (D:\Kiwoom_RtoB) — 개발용
- 실행: Beelink (C:\Kiwoom_RtoB) — 운영
- 데이터: Beelink 단일

PC에서 봇 실행 금지. 모든 데이터는 Beelink에서만 갱신됨.
```

---

# Part 3. Task Scheduler 등록 (daily_quality_logger)

## 3-1. 등록 스크립트 위치 확인

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Test-Path C:\Kiwoom_RtoB\tools\register_daily_logger_task.ps1\""
```

False면 보고하고 중단. True면 진행.

## 3-2. 관리자 권한 등록 — 자동 시도

SSH 세션이 일반 권한이지만 `Start-Process -Verb RunAs` 시도:

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Kiwoom_RtoB\tools\register_daily_logger_task.ps1' -Verb RunAs -Wait\""
```

성공/실패 확인:
```powershell
ssh beelink\lh408@192.168.75.239 "schtasks /Query /TN KiwoomRtoB_DailyQualityLogger /FO LIST"
```

**성공 시:** Part 4로 진행.

**실패 시 (UAC 차단/세션 권한 부족):** 다음 안내 출력 후 Lee 1회 수동 작업 요청:

```
[Lee 수동 작업 필요]
Beelink에 직접 접속(원격 데스크탑 또는 직접) →
PowerShell을 "관리자 권한으로 실행" →
다음 3줄 입력:

cd C:\Kiwoom_RtoB\tools
.\register_daily_logger_task.ps1
schtasks /Query /TN KiwoomRtoB_DailyQualityLogger

마지막 명령 결과에 "KiwoomRtoB_DailyQualityLogger"가 보이면 완료.
완료 후 알려주세요.
```

## 3-3. 등록 후 검증 (Lee 수동 완료 시 또는 자동 성공 시)

```powershell
# 작업 존재 확인
ssh beelink\lh408@192.168.75.239 "schtasks /Query /TN KiwoomRtoB_DailyQualityLogger /FO LIST /V"

# 다음 실행 시각 확인 (NextRunTime 필드)
# 트리거 16:30 정상 등록 여부
# 실행 사용자 = lh408 확인
```

---

# Part 4. 최종 동작 검증

## 4-1. Logger 수동 실행 (현재 상태 그대로)

```powershell
ssh beelink\lh408@192.168.75.239 "cd C:\Kiwoom_RtoB && .venv\Scripts\python.exe tools\daily_quality_logger.py"
```

기대 출력:
- `[일일 평가] 2026-XX-XX: N건 저장`
- `[사후 기록] ...`
- `[마스터] M건`

## 4-2. 마스터 CSV 누적 확인

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-Item C:\Kiwoom_RtoB\candle_quality_master.csv | Select-Object Length, LastWriteTime; (Get-Content C:\Kiwoom_RtoB\candle_quality_master.csv | Measure-Object -Line).Lines\""
```

라인 수 = (이전 누적 + 오늘 신규).

## 4-3. Task Scheduler 시뮬레이션 실행

등록된 작업을 즉시 트리거:
```powershell
ssh beelink\lh408@192.168.75.239 "schtasks /Run /TN KiwoomRtoB_DailyQualityLogger"

# 5초 대기 후 결과 확인
Start-Sleep 5
ssh beelink\lh408@192.168.75.239 "schtasks /Query /TN KiwoomRtoB_DailyQualityLogger /FO LIST /V | findstr /I \"Result Status Last\""
```

`Last Result: 0` = 정상.

---

# Part 5. 최종 보고 양식

```
[봇 실행 위치 진단]
PC 가동: <O/X> + <프로세스/Task 정보>
Beelink 가동: <O/X> + <프로세스/Task 정보>
collection_pool.json mtime: PC=<...>, Beelink=<...>
판정: <PC 운영/Beelink 운영/미가동/중복가동>

[봇 본체 이전] (해당 시)
PC 봇 중단: <성공/실패>
데이터 이전: <파일 수/크기>
Beelink 봇 실행 방식: <보류/등록완료>

[Task Scheduler 등록]
register 스크립트 위치: <확인됨>
자동 등록 시도: <성공/UAC 차단>
Lee 수동 작업 필요 여부: <X/O>
등록 확인: <KiwoomRtoB_DailyQualityLogger 존재>
다음 실행 시각: <YYYY-MM-DD 16:30>

[최종 검증]
Logger 수동 실행: <성공/실패, N건 평가>
마스터 CSV 누적: <M건>
Task 시뮬 실행 (Last Result): <0/기타>
```

---

# 주의사항

- **중복 가동 발견 시 즉시 중단** — 양쪽에서 collection_pool에 쓰면 데이터 오염
- **PC 데이터 디렉토리 삭제 금지** — 백업 가치, dev 환경에서 코드 테스트 시 필요
- **봇 본체 실행 방식 불명확 시 보고** — Part 2-3에서 추측으로 진행하지 말고 Part 1 결과 보고 후 별도 지시 받기
- **시크릿 재전송 불필요** — 이미 Part 1 단계에서 옮겨져 있음
- **git workflow 준수** — 코드 변경 발생 시 PC commit → push → Beelink pull

---

# 작업 순서 요약

```
1. Part 1: 봇 실행 위치 진단 (SSH로 양쪽 확인)
   → 결과에 따라 분기

2. (PC 운영 중일 때만) Part 2:
   - PC 봇 중단
   - 데이터 SCP로 Beelink 이전
   - Beelink 봇 실행 방식은 Part 1 결과 기반 별도 결정/보고

3. Part 3: Task Scheduler 등록
   - 자동 시도 → 실패 시 Lee 수동 안내

4. Part 4: 검증
   - Logger 수동 실행
   - Task 시뮬 실행

5. Part 5: 최종 보고
```
