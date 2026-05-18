# 세션 핸드오프 — 2026-05-18

> 다음 Claude AI가 이 문서만 읽고 작업을 이어받을 수 있도록 작성된 자체완결 요약.
> 이 프로젝트의 영구 원칙은 `~/.claude/projects/d--Kiwoom-RtoB/memory/` 메모리에도 저장됨.

---

## 0. TL;DR

- **이 봇은 원본 키움 자동매매 봇을 최소 수정하여 "매수→수집풀 적재"로 우회한 것.**
- **운영 위치**: PC `D:\Kiwoom_RtoB` = dev only (코드 작성 + git push). Beelink `C:\Kiwoom_RtoB` = 24/7 운영.
- **원본 봇 코드 수정 면적**: `automation/utils/collection_pool.py` 신규 + `rt_search.py`/`websocket.py` 5지점 (chk_n_buy → add_to_pool 교체)
- **추가 신규 모듈**: `sector/candle_quality.py` (D-score 평가), `tools/data_loaders.py` (공용 로더), `tools/daily_quality_logger.py` (일별 평가 자동화), `tools/register_*.ps1` (Task 등록)
- **자동 작업 2개** (Beelink Task Scheduler):
  1. `KiwoomRtoB_Bot` — 봇 데몬 자동 가동/복구 (트리거 = 부팅 시 + 매일 08:30, **`MultipleInstances=IgnoreNew`이라 이미 돌고 있으면 무시**). Beelink는 24/7 켜져있고 봇 프로세스도 한 번 띄우면 계속 같은 인스턴스로 돔. **매일 재부팅이 아님** — 봇이 죽었을 때만 자동 복구되는 이중 안전망
  2. `KiwoomRtoB_DailyQualityLogger` — 매일 16:30, 풀 평가 + 마스터 통합 + 풀 비우기
- **GitHub**: https://github.com/lh4080-alt/Kiwoom_RtoB (HTTPS, private). SSH 22 timeout 환경 → push/clone 모두 HTTPS.

---

## 1. 오늘 발생한 사고 + 조치

### 1.1 사고: cond 명령 실패 (오전 10:49)

**증상**: 사용자가 텔레그램에서 `cond list` / `cond 0` 보냈을 때:
```
조건식 목록을 가져올 수 없습니다.
cond 명령어 실행 중 오류: received 1000 (OK) Bye; then sent 1000 (OK) Bye
```

**진단 (3차 시도 끝에 정확한 원인 도달)**:

1. **1차 진단 (오답)**: "내가 Beelink로 데몬화해서 에러"
   - → 틀림. 원본 봇도 데몬 24/7이고, 코드의 일별 stop/start 로직은 그대로 작동.

2. **2차 진단 (부정확)**: "키움 R10001 중복 접속"
   - → 틀림. 로그 grep 결과 R10001 키워드 0건.

3. **정확한 원인**:
   - **토큰 24시간 만료** → 봇 자동 재발급 (CODE=8005 감지) → 재연결 성공
   - 하지만 **WebSocket 재연결 후 보유종목 REG(실시간 등록) 복구 큐가 무한 반복**
   - [websocket.py:1862-1877](../automation/realtime/websocket.py#L1862-L1877) 로직: "60초 이상 시세 안 들어오면 다시 등록 요청"
   - 끊긴 WebSocket에 등록 요청 보냄 → 시세 안 옴 → 60초 후 또 등록 요청 → 무한 사이클
   - 그 와중에 `cond list` 명령이 끊긴 WebSocket으로 가서 실패

**조치**: 봇 재시작 (Beelink SSH)
```powershell
schtasks /End /TN KiwoomRtoB_Bot
Stop-Process -Id <python.exe 봇 본체 PID> -Force   # ← 좀비 케이스 대응
schtasks /Run /TN KiwoomRtoB_Bot
```

### 1.2 좀비 프로세스 케이스 (주의)

`register_bot_task.ps1`이 `cmd /c "python ... >> bot.log 2>&1"` 구조로 등록됨. `schtasks /End`는 cmd 래퍼만 종료하고 자식 python(봇 본체)은 살아남는 경우 있음 → 새 봇과 좀비 봇 2개 동시 가동 = R10001 위험.

**재시작 절차 (안전판)**:
1. `schtasks /End /TN KiwoomRtoB_Bot`
2. `tasklist /FI "IMAGENAME eq python.exe"` 확인
3. 좀비 PID 있으면 `Stop-Process -Id <PID> -Force`
4. `tasklist` 다시 확인 → 0개
5. `schtasks /Run /TN KiwoomRtoB_Bot`

### 1.3 진단 헛다리 교훈 (다음 Claude를 위한 경고)

오늘 사용자에게 3번 정정했음:
1. "내가 데몬화해서" → 틀림
2. "키움 API의 search_type='1'은 초기 스냅샷 안 보냄" → 기술적으로는 맞지만 사용자 관점에서는 부정확한 일반화 (09:00 시장 개시 시 동시 편입 이벤트 폭발로 결과적으로 채워짐)
3. **단정 전에 코드/로그로 검증할 것**. 추측은 사용자가 명시적으로 금지.

---

## 2. 봇 가동 순서 (원본 코드 그대로)

### 2.1 데몬 가동 (Beelink Task Scheduler)

**트리거 2개 (OR 조건)** — 둘 다 봇 프로세스 자동 시작/복구용. `MultipleInstances=IgnoreNew`라 이미 돌고 있으면 무시 → **봇은 한 번 띄워지면 계속 같은 인스턴스가 24/7 데몬으로 돔**. 매일 재부팅 X.

| 트리거 | 발동 시점 | 역할 |
|---|---|---|
| **AtStartup** | Beelink가 켜질 때 (재부팅/정전 후 등) | 시스템 재가동 시 봇 자동 복구 |
| **Daily 08:30** | 매일 08:30 | 봇이 죽어있을 때만 다시 띄우는 백업 트리거 |

```
KiwoomRtoB_Bot 트리거 (위 둘 중 하나)
  ↓ (이미 봇 돌고 있으면 무시)
cmd.exe /c "C:\Kiwoom_RtoB\.venv\Scripts\python.exe -u core/run_wrapper.py >> logs/bot.log 2>&1"
  ↓ (working dir = C:\Kiwoom_RtoB\automation)
run_wrapper.py
  ├─ SSL 검증 비활성화
  ├─ os.chdir(automation/)
  └─ runpy.run_path('core/main.py')
       ↓
main.py: asyncio.run(main())
  └─ MainApp.run()
       └─ while self.keep_running:   ← 무한 루프 (데몬)
              ├─ 텔레그램 메시지 폴링 (1초마다)
              ├─ check_market_timing()   ← 장 시작/종료 자동 처리
              └─ asyncio.sleep(1)
```

### 2.2 일별 사이클 (원본 코드 내장)

| 시각 | 이벤트 |
|---|---|
| **09:00 정각** | `is_market_start_time()` 트리거 → `auto_start=true`이면 `last_feature_numbers` 기준 자동 start |
| | start_command 내부에서 `token_manager.reset_token()` → 새 토큰 발급 → feature 시작 |
| | 봇이 CNSRREQ 등록 → 시장 개시와 동시에 다수 편입 이벤트 받음 |
| 09:00 ~ 15:30 | 실시간 조건검색 매칭 → `add_to_pool` 호출 → `collection_pool.json` 누적 |
| **15:30** | `is_market_end_time()` 트리거 → `stop_all()` 호출 → feature 정지 (봇 프로세스는 살아있음) |
| 15:30 ~ | 텔레그램으로 "장 마감 자동매매 종료" 메시지 |
| **16:30** | KiwoomRtoB_DailyQualityLogger Task 트리거 |
| | → evaluate_today_pool → daily CSV 저장 |
| | → backfill_returns → 과거 d1/d5 수익률 채움 |
| | → rebuild_master → master CSV 통합 |
| | → **clear_pool → collection_pool.json = {}** ← 다음 거래일 빈 풀로 시작 |
| 다음 거래일 09:00 | 빈 풀로 시작 → 새 매칭 누적 |

### 2.3 핵심 코드 위치

| 동작 | 파일:라인 |
|---|---|
| 무한 루프 (데몬) | [main.py:303-314](../automation/core/main.py#L303-L314) |
| auto_start 트리거 | [main.py:167-186](../automation/core/main.py#L167-L186) |
| stop_all 트리거 | [main.py:188-231](../automation/core/main.py#L188-L231) |
| CNSRREQ 등록 (조건식) | [websocket.py:748-753](../automation/realtime/websocket.py#L748-L753), [rt_search.py:265-271](../automation/realtime/rt_search.py#L265-L271) |
| **조건식 매칭 → 풀 적재** (우리 수정) | [websocket.py:500](../automation/realtime/websocket.py#L500), [rt_search.py:149](../automation/realtime/rt_search.py#L149) |
| 풀 적재 함수 (신규 모듈) | [collection_pool.py:add_to_pool](../automation/utils/collection_pool.py) |
| 60초 무응답 시 REG 복구 (무한 큐 원인) | [websocket.py:1862-1877](../automation/realtime/websocket.py#L1862-L1877) |
| 토큰 24h 만료 + 자동 재발급 | [token_manager.py](../automation/telegram/commands/token_manager.py), [websocket.py:991-1010](../automation/realtime/websocket.py#L991-L1010) |

---

## 3. 데이터 흐름

```
[원천 데이터]
\\beelink\market_data\           ← 별도 프로젝트 market_data_collector가 채움
├── bars_1m/stocks/{code}/{YYYY}/{YYYYMM}.parquet   1분봉
├── bars_1m/index/{code}/...                       지수(001=KOSPI, 101=KOSDAQ)
└── universe.db                                     SQLite 종목 마스터

[봇 런타임 데이터]
C:\Kiwoom_RtoB\config\data\
├── settings.json              is_paper_trading, last_feature_numbers, search_seq 등
├── collection_pool.json       조건검색 매칭 종목 누적 (매일 16:30 비워짐)
└── last_held_stocks.json      현재 보유 종목 (봇이 자동 동기화)

[D-score 산출물]
C:\Kiwoom_RtoB\
├── candle_quality_daily/YYYY-MM-DD.csv   매일 풀 평가 결과 (영구 보존, DB 역할)
└── candle_quality_master.csv             일별 CSV 통합본
```

---

## 4. 필터링 과정 (4단계)

### 단계 1: HTS 조건검색 (1차 필터)
- 사용자가 키움 HTS에서 만든 조건식 (예: "상승지속후 눌림목")
- 항목: 기간내 등락률 +7%↑, 어제 음봉, 오늘 양봉, 10/20일선 위 등
- 키움 서버가 실시간 평가
- search_type='1'로 등록 → 편입(I)/이탈(D) 이벤트 push
- **중요**: 초기 스냅샷은 안 보냄. 단 09:00 시장 개시 시 다수 종목이 동시 편입 → 결과적으로 그 시점 HTS 충족 종목이 한꺼번에 봇으로 들어옴

### 단계 2: 수집풀 적재 (봇 수정 지점)
- WebSocket에서 매칭 이벤트 수신 → `add_to_pool(stk_cd, condition_name, seq_id)` 호출
- 같은 종목 재매칭 시 `hit_count` 증가, `last_seen` 갱신
- 영구 보존 X — 매일 16:30에 비워짐

### 단계 3: D-score 평가 (16:30 자동 + 수동 가능)

`tools/daily_quality_logger.py`가 풀의 각 종목에 대해:

1. `tools/data_loaders.py::load_7d_bars(code)` 호출
   - `\\beelink\market_data\bars_1m\stocks\{code}\...` 1분봉 parquet 읽기
   - 일자별 groupby → 최근 7일 일봉 OHLCV
2. `sector/candle_quality.py::evaluate_candle_quality(bars_7d)` 호출
   - 7개 항목 평가 (D1~D7), 0~10점

**D-score 7개 항목**:

| 항목 | 만점 | 의미 |
|---|---|---|
| D1 | 0~2 | 주도 양봉 강도 (5%↑ 양봉 중 최대) — 윗꼬리 짧음 +1, 몸통 김 +1 |
| D2 | 0~1 | 주도 양봉 위치 — 2~4일 전(윈도우 중간)이면 +1 |
| D3 | 0~2 | 눌림 깊이 — 주도 양봉 고가 대비 그 후 최저가 하락폭. 3~8% +2 (이상적), 0~3% 또는 8~12% +1 |
| D4 | 0~1 | 양봉 우세 — 7일 중 4일 이상 양봉 +1 |
| D5 | 0~1 | 평균 윗꼬리 — 양봉들의 윗꼬리 평균 < 25% +1 |
| D6 | 0~2 | 당일 캔들 — 양봉 + 종가 위치 70%↑ +1, 윗꼬리 < 20% +1 |
| D7 | 0~1 | 거래량 패턴 — 주도일 급증 + 조정일 감소 + 당일 회복 (3 AND 조건) +1 |

3. 결과 → `candle_quality_daily/{YYYY-MM-DD}.csv`에 저장
   - 컬럼: `eval_date, code, today_close, score, pullback_pct, bullish_ratio, avg_wick, hit_count, first_seen, last_seen, seq_ids, kospi_chg, kosdaq_chg, eval_time, d1_close, d1_return_pct, d5_close, d5_return_pct, D1, D2, D3, D4, D5, D6, D7`
4. `backfill_returns` — 이전 거래일 CSV의 d1/d5 수익률을 사후 채움
5. `rebuild_master` — 일별 CSV 모두 합쳐 `candle_quality_master.csv`
6. **`clear_pool` — collection_pool.json 비움**

### 단계 4: 매수 결정 (필터링 모듈 — 향후 구현)

현재 미구현. 향후 다음 정보 활용 가능:

| 신호 | 출처 | 활용 예 |
|---|---|---|
| D-score | candle_quality | 임계값 (예: 7+ 만 매수) |
| hit_count | collection_pool | 빈도 가중치 (높을수록 활발/출렁임) |
| **이력 가산** | `data_loaders.lookup_history(code, days=N)` | 과거 N일 daily CSV에 등장한 횟수 → "지속적 신호" 가산점 |
| 시장 컨디션 | `load_market_change(date)` | KOSPI/KOSDAQ 약세면 D6 가중치 조정 등 |
| 거래량 급증 | (별도 구현 필요) | +500%↑ 종목 가점 |

`lookup_history` 결과 예:
```python
{
    'appearances': 3,
    'last_seen_date': '2026-05-15',
    'dates': ['2026-05-13', '2026-05-15', '2026-05-18'],
    'history': [
        {'date': '2026-05-13', 'score': 6, 'hit_count': 45},
        {'date': '2026-05-15', 'score': 8, 'hit_count': 24},
        {'date': '2026-05-18', 'score': 8, 'hit_count': 87},
    ]
}
```

---

## 5. 오늘 코드 변경 누적

원본 봇 코드 손대지 않은 새 기능들 + 1개 모듈만 신규 추가, 원본 2개 파일 5지점 수정:

| 영역 | 파일 | 변경 |
|---|---|---|
| 원본 봇 수정 | `automation/utils/collection_pool.py` | **신규**: `add_to_pool`, `get_pool`, `clear_pool` |
| 원본 봇 수정 | `automation/realtime/rt_search.py` | 2지점: `chk_n_buy` → `add_to_pool` 임포트/호출 |
| 원본 봇 수정 | `automation/realtime/websocket.py` | 3지점: 같은 교체 + `_safe_chk_n_buy` → `_safe_add_to_pool` 이름·본문 교체 |
| 신규 모듈 | `sector/candle_quality.py` | 7일 캔들 D-score (0~10) 평가 |
| 신규 모듈 | `tools/data_loaders.py` | `load_today_pool_codes`, `load_today_pool_full`, `load_7d_bars`, `lookup_close`, `load_market_change`, `lookup_history` |
| 신규 모듈 | `tools/daily_quality_logger.py` | 매일 16:30 자동 실행 — 평가 + backfill + master + 풀 비우기 |
| 신규 모듈 | `tools/test_candle_quality_distribution.py` | 분포 분석 도구 (수동 실행용) |
| 인프라 | `requirements.txt` | pandas, httpx, requests, websockets, pyarrow |
| 인프라 | `.gitignore` | 시크릿/데이터/캐시 차단 |
| 인프라 | `tools/register_bot_task.ps1` | KiwoomRtoB_Bot Task 등록 (관리자 권한 PowerShell, RunAs로 SSH에서도 가능) |
| 인프라 | `tools/register_daily_logger_task.ps1` | KiwoomRtoB_DailyQualityLogger Task 등록 |
| 사고로 dead code | `automation/trading/check_n_buy.py` | `chk_n_buy` 함수 본체 그대로 보존 (호출 경로 없음). 향후 매수 모듈에서 재사용 가능 |

**누적 원본 봇 수정**: 1개 파일 신규 + 2개 파일 5지점. 최소.

---

## 6. 환경 정보

| 항목 | 값 |
|---|---|
| PC (dev) | Windows 11, `D:\Kiwoom_RtoB`, Python 3.14 |
| Beelink (운영) | Windows, `C:\Kiwoom_RtoB`, Python 3.14.3, venv `.venv` |
| SSH 별칭 | `beelink` (이미 ~/.ssh/config 설정됨, 키 인증) |
| Beelink 사용자 | `lh408` |
| Beelink 호스트 | 192.168.75.239 |
| 키움 키 | 실계좌 키 4개 (real_app_key/secret, paper_*는 미사용), 텔레그램 토큰 |
| 키움 토큰 | 24시간 만료. start_command이 reset_token + 재발급 |
| settings.json 핵심 | `is_paper_trading: false`, `auto_start: true`, `last_feature_numbers: "1"`, `search_seq: ["0","1","2"]` |
| GitHub | https://github.com/lh4080-alt/Kiwoom_RtoB (HTTPS, private). SSH 22 timeout 환경이라 HTTPS만 사용 |

---

## 7. 영구 운영 원칙 (필독)

이 프로젝트는 메모리에 다음 두 가지가 영구 저장돼 있음 (자동 로드됨):

1. **`feedback-minimal-modifications`**: 원본 봇 수정 면적 최소화. 부가 리팩터 / 정리 / "기왕 손대는 김에" 금지. 호출만 바꿔도 되면 원래 함수 본체는 dead code로 보존. 신규 기능은 가능한 한 신규 모듈로 분리.
2. **`project-rebuild-from-original`**: PC dev / Beelink 운영 / 데이터 Beelink 단일. 봇 PC에서 절대 실행 금지 (키움 중복 접속).

추가 운영 원칙:
- **추측 금지**: 단정적 진단 전에 코드/로그로 검증. 사용자가 "추측 말고 확인"을 명시적으로 요청한 적 있음.
- **헛다리 짚으면 즉시 인정**: 변명 X, 정확한 정정 + 새 가설.
- **사용자 결정 사안에 임의로 진행 X**: 4가지 옵션 같은 결정은 사용자에게 선택받음.

---

## 8. 다음 작업 후보 (현재 미정)

- 매수 결정 필터링 모듈 (단계 4) — D-score + hit_count + lookup_history + 거래량 + 시장 컨디션 결합 정책 결정 필요
- D7 (거래량 패턴) 통과율 0% 문제 — 임계값(1.5/0.7/1.2배) 완화 검토
- 표본 30+ 누적 후 분포 재평가 + 항목별 가중치 재조정 (instruction_candle_quality_v2.md의 Part C 참조)
- WebSocket 재연결 후 REG 큐 무한 반복 — 원본 봇 한계. 봇 재시작 외 근본 해결은 원본 수정 필요 (minimum-modification 위배)
