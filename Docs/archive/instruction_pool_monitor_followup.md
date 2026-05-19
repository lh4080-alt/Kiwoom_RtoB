# 지시서: 5분 홀딩 모니터링 + 2차 대기풀 — 후속 통합 보완

## 적용 시점
**본 작업("5분 홀딩 모니터링 + 2차 대기풀") 완료 후** 순차 적용.

## 본 작업 사전 확정 사항 (전제)
- FID 체결강도 추출 확정 (FID 검증으로 결정)
- 0B 한도 max_subscriptions: 95
- 인메모리 dict + 30초 flush (`sector/pool_buffer.py`)
- 원본 봇 수정 = `_handle_stock_quote` 1줄 dispatch
- 신규 모듈: `sector/pool_monitor.py`, `sector/pool_buffer.py`
- 가격 -2% 지속 15초 / 체결강도 5분평균 110 / 거래량 baseline 비교 0.5
- 2차 풀 10분 후 마지막 5분 재확인
- 본 작업 6/7번 = 기본 로깅(logger.info 한 줄씩) / 기본 cleanup(try/finally) — 본 보완 지시서 작업 1/2는 그 위에 상세 확장

## 작업 1: 위반 통계 로깅

### 1-1. 위반 로그 파일
- 경로: `C:\kiwoom_RtoB\logs\pool_violations.csv`

### 1-2. 스키마
```csv
timestamp,code,pool_stage,event,reason,signal_price,current_price,strength_avg,volume_recent,volume_baseline,duration_sec,d_score
```

| 필드 | 설명 |
|---|---|
| `pool_stage` | `primary` / `secondary` |
| `event` | `entered` / `violated` / `passed` / `discarded` / `bought` |
| `reason` | `price_drop_2pct` / `weak_strength` / `volume_drop` / `passed` / null |

### 1-3. 기록 시점
- 풀 진입 → `entered`
- 1순위 위반 확정 → `violated` (`reason=price_drop_2pct`)
- 5분 만료 위반 → `violated` (`weak_strength` / `volume_drop`)
- 5분 만료 통과 → `passed`
- 2차 풀 진입 → `entered` (`pool_stage=secondary`)
- 10분 만료 폐기 → `discarded`
- 10분 만료 통과 → `passed`
- 매수 실행 → `bought`

### 1-4. helper 위치
`sector/pool_monitor.py`에 `log_pool_event(code, pool_stage, event, reason, task_data, current_price)` 함수.

### 1-5. 일별 집계
- `tools/daily_violation_summary.py` 신규
- 출력: `logs/violation_summary.csv` 누적
- 집계 항목: primary entered/passed, price/strength/volume 위반, secondary entered/passed/discarded, total_bought

### 1-6. daily_quality_logger 통합
`__main__`에 `daily_summary(today)` 호출 추가.

### 1-7. 텔레그램 일별 알림 (선택)
yaml `logging.telegram_daily_summary: true`일 때만.

## 작업 2: 0B cleanup 패턴 강화

### 2-1. try/finally 패턴
`sector/pool_monitor.py`의 `_monitor_primary_pool`, `_monitor_secondary_pool`에:
- `try / except CancelledError / except Exception / finally`
- finally: `_unregister_stock_quotes(code)` + 풀 dict 제거
- 모든 분기에 `log_pool_event(...)` 호출

### 2-2. 봇 startup 풀 초기화
`automation/core/run_wrapper.py` 또는 startup hook에:
```python
self.pool_monitor._pending.clear()
self.pool_monitor._secondary.clear()
```

### 2-3. WebSocket 재연결 시 재등록
`on_websocket_reconnect()`에서 활성 풀 종목 0B 재등록. 실패 시 풀에서 제거.

### 2-4. 0B 한도 경고
- ≥ 80% (76개): warning 로그
- ≥ 95% (90개): 텔레그램 critical
- ≥ 100% (95개): 신규 등록 차단 (RuntimeError)

## 작업 3: 1주 운영 후 임계값 재조정

### 3-1. 분석 트리거
- `logs/pool_violations.csv` 누적 ≥ 100건, 또는
- 운영 ≥ 7거래일

### 3-2. 분석 모듈
`tools/analyze_pool_thresholds.py`:
- 위반 사유 분포
- 1차 풀 통과율
- 2차 풀 회복률
- 임계 적정성 진단 (80%↑ 위반 → 완화 검토 / 5%↓ → 강화 검토)

### 3-3. 조정 결정 트리

| 1차 통과율 | 진단 |
|---|---|
| > 70% | 임계 너무 관대, 강화 검토 |
| 30~70% | 적정, 유지 |
| < 30% | 임계 너무 빡빡, 완화 검토 |

| 2차 회복률 | 진단 |
|---|---|
| > 50% | 1차 임계 너무 빡빡 |
| 10~50% | 적정 |
| < 10% | 2차 풀 실효성 낮음, 단순화 검토 |

### 3-4. 조정 적용
`config/pool_monitor.yaml` 수정만으로 적용. 코드 수정 X.

## 적용 순서
```
[본 작업 완료]
  ↓
[작업 1: 로깅 강화] — 즉시
  ↓ 운영 시작
[작업 2: cleanup 강화] — 즉시
  ↓ 운영
[7거래일 데이터 누적]
  ↓
[작업 3: 임계 재조정 분석 + yaml 수정]
```

## 변경 면적
- 신규: `tools/daily_violation_summary.py`, `tools/analyze_pool_thresholds.py`
- 수정: `sector/pool_monitor.py` (logging 호출 + cleanup 강화)
- 수정: `tools/daily_quality_logger.py` (풀 통계 통합)
- 수정: `automation/core/run_wrapper.py` 또는 startup hook (1줄, 풀 초기화)
- 원본 봇 추가 수정 없음

## git workflow 4단계
PC commit → push → Beelink pull → 봇 재시작 (Task Scheduler 자동)
