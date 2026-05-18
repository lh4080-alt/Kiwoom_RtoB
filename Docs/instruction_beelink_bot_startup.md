# 지시서: Beelink에서 kiwoom_RtoB 봇 본체 가동

## 전제 (협상 불가)
- **봇은 Beelink에서 24/7 가동.** PC는 dev 전용 (코드 작성 → git push만).
- **데이터는 Beelink 단일.** PC에 데이터 쌓이는 시나리오 없음.
- **옵션 분기 없음.** 위 패턴이 이미 결정된 운영 방식. 다른 옵션 제시 금지.
- 이 패턴은 다른 봇들(External Factors, Theme Scanner, sector_intraday 등) 모두 동일.

## 범위
1. **Part 1**: Beelink 봇 본체 엔트리포인트 식별
2. **Part 2**: 가동 방식 결정 (Task Scheduler 트리거 or 데몬 상시)
3. **Part 3**: 자동 시작 등록 (Beelink Task Scheduler)
4. **Part 4**: 봇 가동 + 정상 동작 검증
5. **Part 5**: 최종 보고

**클로드코드가 SSH로 전부 자동 수행. Lee 개입은 UAC 차단 시 1회 수동만 (없을 가능성 높음 — 이전 단계도 자동 통과했음).**

---

# Part 1. 봇 엔트리포인트 식별

## 1-1. main 스크립트 찾기

Beelink `C:\kiwoom_RtoB`에서 봇 메인 진입점 후보 탐색:

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-ChildItem C:\kiwoom_RtoB -Filter 'main*.py' -Recurse | Select-Object FullName\""
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-ChildItem C:\kiwoom_RtoB -Filter 'run*.py' -Recurse | Select-Object FullName\""
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-ChildItem C:\kiwoom_RtoB -Filter '*bot*.py' -Recurse | Select-Object FullName\""
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-ChildItem C:\kiwoom_RtoB -Filter 'app*.py' -Recurse | Select-Object FullName\""
```

후보 발견하면 각 파일의 `if __name__ == '__main__':` 블록 확인:

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Select-String -Path C:\kiwoom_RtoB\*.py -Pattern '__main__' -List\""
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Select-String -Path C:\kiwoom_RtoB\**\*.py -Pattern '__main__' -List\""
```

## 1-2. 봇 가동 방식 분석

엔트리포인트 코드 첫 50줄 + main 블록 확인:

```powershell
ssh beelink\lh408@192.168.75.239 "type C:\kiwoom_RtoB\<엔트리포인트경로>"
```

다음 패턴 식별:
- **상시 가동 (데몬)**: `while True:` / `asyncio.run(main())` / 무한 루프 / 9:00~15:30 내부 체크 → 한 번 실행하면 종료 없이 계속 돔
- **시간 트리거**: 단발 실행 후 종료 → cron/Task Scheduler가 매번 실행
- **시작/종료 트리거**: 09:00 시작 / 15:30 종료 명시적 분리

## 1-3. README/문서 확인

```powershell
ssh beelink\lh408@192.168.75.239 "type C:\kiwoom_RtoB\README.md 2>NUL"
ssh beelink\lh408@192.168.75.239 "type C:\kiwoom_RtoB\docs\*.md 2>NUL"
```

가동 방식 명시되어 있으면 그대로 따름.

## 1-4. 엔트리포인트 식별 실패 시

후보 여러 개이거나 main 블록이 없거나 모호하면 **즉시 보고하고 중단**. Lee에게 다음 질문:
- 봇 메인 파일이 무엇인가
- 가동 방식이 데몬인가 시간 트리거인가

이 부분은 추측하지 않음.

---

# Part 2. 가동 방식 결정

Part 1 결과에 따라:

## 2-A. 데몬형 (상시 가동)

봇이 한 번 시작하면 자체 시간 체크로 09:00~15:30 동작.

**Task Scheduler 트리거 1개 등록:**
- **트리거**: 시스템 부팅 시 + 매일 08:30 (재시작 안전망)
- **동작**: `.venv\Scripts\python.exe <엔트리포인트>`
- **재시작 정책**: 실패 시 1분 후 자동 재시도, 최대 3회
- **다중 실행 방지**: "If task is already running: Do not start a new instance"

## 2-B. 시간 트리거형

봇이 단발 실행 후 종료. 매번 Task Scheduler가 실행.

**Task Scheduler 트리거 2개 등록 (예시, 봇 동작에 맞춰 조정):**
- **트리거 1**: 매일 08:50 시작 작업
- **트리거 2**: 매일 15:35 종료 작업 (필요 시)

또는 코드 내부에 09:00~15:30 루프 있으면 2-A로 처리.

**Part 1 결과에 따라 자동 선택. 모호하면 보고 후 중단.**

---

# Part 3. 자동 시작 등록

## 3-1. 등록 스크립트 작성

PC `D:\Kiwoom_RtoB\tools\register_bot_task.ps1` 생성:

```powershell
# ============================================
# kiwoom_RtoB 봇 본체 Task Scheduler 등록
# 관리자 권한 PowerShell에서 실행
# ============================================

$TaskName = "KiwoomRtoB_Bot"
$BotEntry = "C:\kiwoom_RtoB\<엔트리포인트>"  # Part 1에서 식별된 경로
$PythonExe = "C:\kiwoom_RtoB\.venv\Scripts\python.exe"
$WorkingDir = "C:\kiwoom_RtoB"

# 기존 작업 제거 (재등록 안전)
schtasks /Delete /TN $TaskName /F 2>$null

$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $BotEntry `
    -WorkingDirectory $WorkingDir

# 트리거: 부팅 시 + 매일 08:30 안전망
$trigger1 = New-ScheduledTaskTrigger -AtStartup
$trigger2 = New-ScheduledTaskTrigger -Daily -At "08:30"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($trigger1, $trigger2) `
    -Settings $settings `
    -RunLevel Highest `
    -User "lh408" `
    -Description "kiwoom_RtoB main bot (auto-restart on boot + daily 08:30)"

Write-Host "Task '$TaskName' registered."
schtasks /Query /TN $TaskName /FO LIST /V
```

**중요:** Part 1에서 식별된 정확한 엔트리포인트 경로를 `$BotEntry`에 박아넣음. 추측 금지.

데몬이 아니라 시간 트리거형(2-B)이면 트리거를 시간 트리거로 변경.

## 3-2. PC commit + push

```powershell
cd D:\Kiwoom_RtoB
git add tools\register_bot_task.ps1
git commit -m "Add bot main task scheduler registration script"
git push origin main
```

## 3-3. Beelink git pull

```powershell
ssh beelink\lh408@192.168.75.239 "cd C:\kiwoom_RtoB && git pull origin main"
```

## 3-4. Beelink에서 자동 등록

이전 단계(daily_quality_logger 등록)와 동일 방식. UAC 자동 통과 확인됨:

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\kiwoom_RtoB\tools\register_bot_task.ps1' -Verb RunAs -Wait\""
```

## 3-5. 등록 확인

```powershell
ssh beelink\lh408@192.168.75.239 "schtasks /Query /TN KiwoomRtoB_Bot /FO LIST /V"
```

실패 시(UAC 차단 등) Lee 1회 수동 안내. 성공 가능성 높음 — 이전 단계 통과했으면 이번도 통과.

---

# Part 4. 봇 가동 + 검증

## 4-1. 봇 즉시 시작

```powershell
ssh beelink\lh408@192.168.75.239 "schtasks /Run /TN KiwoomRtoB_Bot"
Start-Sleep 5
```

## 4-2. 프로세스 확인

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-WmiObject Win32_Process -Filter 'Name=''python.exe''' | Where-Object { `$_.CommandLine -like '*kiwoom_RtoB*' } | Select-Object ProcessId, CommandLine | Format-List\""
```

kiwoom_RtoB 관련 python 프로세스 확인되어야 함.

## 4-3. 로그 확인

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-ChildItem C:\kiwoom_RtoB\logs\ | Sort-Object LastWriteTime -Descending | Select-Object -First 3\""

# 최신 로그 마지막 50줄
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-Content (Get-ChildItem C:\kiwoom_RtoB\logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName -Tail 50\""
```

에러 메시지(`Error`, `Exception`, `Traceback`) 검색:
```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Select-String -Path C:\kiwoom_RtoB\logs\*.log -Pattern 'Error|Exception|Traceback' | Select-Object -Last 20\""
```

## 4-4. collection_pool.json 갱신 여부 확인

봇이 정상이면 일정 시간 후 collection_pool.json mtime 갱신됨:

```powershell
ssh beelink\lh408@192.168.75.239 "powershell -Command \"Get-Item C:\kiwoom_RtoB\config\data\collection_pool.json | Select-Object FullName, Length, LastWriteTime\""
```

장 시간 밖이면 mtime 갱신 없을 수 있음 (정상). 장 시간 중이면 수 분 내 갱신되어야 함.

## 4-5. Task 상태 종합 확인

```powershell
ssh beelink\lh408@192.168.75.239 "schtasks /Query /TN KiwoomRtoB_Bot /FO LIST /V"
# Last Result 필드 확인:
#   0 = 정상
#   0x41301 = 현재 실행 중 (데몬형이면 정상)
#   기타 = 에러
```

---

# Part 5. 최종 보고

```
[봇 엔트리포인트]
파일: <C:\kiwoom_RtoB\...>
가동 방식: <데몬/시간 트리거>

[자동 시작 등록]
register_bot_task.ps1: <PC 작성 + git push 완료>
Beelink 등록: <성공/실패>
Lee 수동 작업 필요: <X/O>
다음 자동 시작: 부팅 시 + 매일 08:30

[봇 가동 검증]
프로세스 가동: <PID / CommandLine>
로그 정상: <O/X, 최근 에러 N건>
collection_pool.json mtime: <YYYY-MM-DD HH:MM>
Task Last Result: <0/0x41301/기타>

[운영 상태]
PC: dev 전용 (D:\Kiwoom_RtoB) — 봇 가동 없음 확인
Beelink: 운영 (C:\kiwoom_RtoB) — 봇 24/7 가동 시작
데이터: Beelink 단일
```

---

# 주의사항

- **PC에서 봇 실행하지 않음.** 어떤 옵션도 제시하지 않음. Beelink만.
- **엔트리포인트 식별 실패 시 추측 금지.** 즉시 보고.
- **로그 없으면 logs/ 디렉토리 비어있을 수 있음.** 봇이 stdout으로만 출력하는 구조면 Task Scheduler 리다이렉트 추가 필요할 수 있음. 4-3에서 로그 0개면 보고.
- **장 시간 밖에 가동 검증하는 경우** collection_pool 갱신 안 됨이 정상. 프로세스 가동 + 로그 정상 + Task Last Result만 확인.
- **기존 daily_quality_logger Task와 충돌 없음.** 별개 작업, 별개 트리거.

---

# 작업 순서

```
1. SSH로 Beelink 봇 엔트리포인트 식별 (Part 1)
   → 모호하면 즉시 보고 중단

2. 데몬/시간트리거 판정 → 등록 스크립트 작성 (Part 2, 3-1)

3. PC commit + push → Beelink git pull (Part 3-2, 3-3)

4. Beelink Task Scheduler 자동 등록 (Part 3-4)
   → UAC 차단 시 Lee 1회 수동 안내 (이전 단계 통과했으므로 가능성 낮음)

5. 봇 즉시 시작 + 검증 (Part 4)

6. 최종 보고 (Part 5)
```
