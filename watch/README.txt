=== kiwoom_RtoB 로그 모니터 ===

PC에서 더블클릭으로 Beelink 봇 로그를 실시간으로 보는 .bat 파일들.

【파일별 용도】

  watch_log.bat      — 전체 로그 실시간 (Tail 50줄부터)
  watch_errors.bat   — 에러/Exception/Traceback/CRITICAL/failed 만
  watch_pool.bat     — 1차/2차 풀, 매수, d_score, 수집풀 관련만
  watch_daily.bat    — Daily task (16:30 evaluate/backfill/master/clear) + startup

【사용법】

  1. 파일 더블클릭 → 콘솔창 뜸
  2. 실시간 로그가 줄 단위로 출력됨
  3. 종료: Ctrl+C

여러 창 동시 가동 OK. 예: watch_log + watch_errors 동시 띄워서 전체 흐름과
이상 신호를 분리해서 보기.

【조회 가능한 키워드】

  watch_pool 키워드: primary, secondary, BUY:, d_score, pool entered,
                     pool passed, pool violated, 수집풀, daily buy
  watch_daily 키워드: daily, evaluate, backfill, rebuild_master, clear_pool,
                      DailyTaskManager, startup

다른 키워드로 보려면 cmd 직접:
  ssh beelink "Get-Content C:\Kiwoom_RtoB\logs\bot.log -Tail 200 | Select-String '키워드'"

【시간대 안내】

로그 timestamp는 KST. Beelink가 KST 시간대 + KSTFormatter 적용됨.
예: 2026-05-19 10:51:26,230 [INFO] core.daily_task: DailyTaskManager started

【트러블슈팅】

- SSH 비밀번호 묻는 경우 → ~/.ssh/config 의 키 인증 확인
- 한글 깨짐 → 각 .bat 의 첫 줄 chcp 65001 적용됨. 그래도 깨지면 PowerShell 안에서
  $OutputEncoding = [System.Text.UTF8Encoding]::new() 추가 필요
- 로그 안 흐름 → 봇 정지 또는 bot.log 파일 rotate. 확인:
  ssh beelink "Get-ChildItem C:\Kiwoom_RtoB\logs\ | Sort-Object LastWriteTime -Desc | Select-Object -First 3"

【git】

이 디렉토리는 PC 로컬 모니터 도구라 git에 push 안 함 (.gitignore 또는 그냥 commit 안 함).
