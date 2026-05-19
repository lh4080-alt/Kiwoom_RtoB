# 수집풀 + 필터링 시스템 레퍼런스

> 다른 Claude Code 세션이 이 문서만 읽고 수집풀/풀모니터 동작을 이해할 수 있도록 작성됨.
> 코드 발췌는 짧게, 위치는 정확하게 (file:line).

---

## 1. 전체 흐름 (한 장 요약)

```
[HTS 조건검색 매칭]
       │
       ▼
[키움 WebSocket]
       │
       ├─ CNSRREQ 응답 (초기 스냅샷, search_type='1')   ┐
       │   automation/realtime/websocket.py:365-405    │
       │                                                │  같은 함수 둘 호출:
       └─ REAL 응답 (실시간 신규 매칭)                  │   1) add_to_pool        → 수집풀 적재
           automation/realtime/websocket.py:481-506    │   2) evaluate_and_add   → D-score 평가 → 1차 풀
                                                       ┘
                          │
            ┌─────────────┴──────────────┐
            ▼                            ▼
┌──────────────────────┐    ┌──────────────────────────────┐
│  수집풀(JSON 영구)   │    │  PoolMonitor(메모리)          │
│  collection_pool.json│    │  D-score >= D_SCORE_MIN(=6)   │
│  (필터링/통계용)     │    │  만 1차 풀 진입                │
│  config/data/        │    │  sector/pool_monitor.py       │
└──────────────────────┘    └──────────────────────────────┘
            │                            │
            │ (장 마감 16:30)             │
            ▼                            ▼
   ┌────────────────────┐        [1차 5분]
   │ DailyTaskManager   │        [위반 → 2차 10분]
   │  - evaluate today  │        [통과 → 매수 (DAILY_BUY_LIMIT=5)]
   │  - backfill returns│
   │  - rebuild master  │
   │  - clear pool      │
   └────────────────────┘
```

**핵심 분리**:
- **수집풀 (collection_pool.json)**: HTS 조건검색 매칭의 영구 로그 (필터링/통계용, 매수와 무관)
- **PoolMonitor (인메모리)**: 매수 대상 후보군의 실시간 모니터링 (1차/2차 풀 + 매수 결정)

---

## 2. 수집풀 (collection_pool.json)

**역할**: 조건검색에 매칭된 모든 종목의 누적 로그. 매수 동작 안 함.

**구현**: [automation/utils/collection_pool.py](../automation/utils/collection_pool.py)

**파일**: `config/data/collection_pool.json`

**스키마**:
```json
{
  "016380": {
    "stk_cd": "016380",
    "first_seen": "2026-05-19 12:17:54",
    "last_seen": "2026-05-19 12:17:54",
    "hit_count": 1,
    "conditions": [],          // 조건식 이름 (settings.json cond 매핑 시)
    "seq_ids": ["0"]           // 조건식 일련번호
  }
}
```

**핵심 API**:
- `async add_to_pool(stk_cd, condition_name=None, seq_id=None)` — 신규 추가/기존 갱신 (asyncio.Lock)
- `get_pool() -> dict` — 전체 조회
- `clear_pool() -> int` — 비우기 (장 마감 daily_task에서만 호출, 외부 프로세스 호출 금지)

**원자성**: `tmp + os.replace`로 파일 쓰기 (부분 쓰기 방지).

---

## 3. PoolMonitor — 1차/2차 풀 + 매수

**구현**: [sector/pool_monitor.py](../sector/pool_monitor.py)

### 3.1 임계 상수 (모듈 상단, sector/pool_monitor.py:32-56)

| 상수 | 값 | 의미 |
|---|---|---|
| `D_SCORE_MIN` | 6 | 1차 풀 진입 D-score 임계 (2026-05-19 7→6 하향, 통과율 4.5%→13.6%) |
| `PRIMARY_WAIT_SEC` | 300 | 1차 풀 만료 시간 (5분) |
| `PRICE_BREAK_THRESHOLD` | 0.98 | -2% 가격 위반 기준 |
| `PRICE_BREAK_PERSIST_SEC` | 15 | -2% 지속 시간 (이만큼 지속 시 즉시 2차 풀) |
| `STRENGTH_MIN` | 110 | 체결강도 평균 최소값 |
| `VOLUME_DROP_RATIO` | 0.5 | 마지막 1분 거래량 / baseline 임계 (미달 시 위반) |
| `VOLUME_RECENT_WINDOW_SEC` | 60 | "최근 거래량" 윈도우 (1분) |
| `SECONDARY_WAIT_SEC` | 600 | 2차 풀 대기 시간 (10분) |
| `SECONDARY_RECHECK_WINDOW_SEC` | 300 | 2차 재평가 윈도우 (마지막 5분) |
| `MAX_SUBSCRIPTIONS` | 95 | 0B 실시간 등록 한도 |
| `WARNING_THRESHOLD` / `CRITICAL_THRESHOLD` | 0.80 / 0.95 | 등록 한도 경고/거부 비율 |
| `DAILY_BUY_LIMIT` | 5 | 하루 최대 매수 종목 수 |
| `TOP_N_INITIAL` | 3 | 초기 무조건 매수 슬롯 (이후 슬롯은 d_score 비교) |
| `BUY_QUANTITY` | 1 | 매수 수량 (주) |

### 3.2 진입 조건 (1차 풀)

[sector/pool_monitor.py:85-130 `add_to_pool`](../sector/pool_monitor.py)

| 체크 항목 | 위반 시 동작 |
|---|---|
| `_daily_limit_reached` | skip |
| `d_score < D_SCORE_MIN` (6) | skip (조용히) |
| 이미 1차 또는 2차 풀에 있음 | skip |
| 이미 보유 종목 (`ws.portfolio[code].quantity > 0`) | skip (로그 남김) |
| 0B 등록 한도(95%) 초과 | skip (warning) |
| 위 모두 통과 | 1차 풀 진입 + 0B 등록 + 5분 모니터링 task 시작 |

**진입 시 저장 메타**:
```python
{
  'pool_stage': 'primary',
  'signal_time': datetime.now(),
  'signal_price': float,      # 매칭 시점 가격 (일봉 마지막 close)
  'signal_volume': int,       # 매칭 시점 거래량
  'volume_baseline': int,     # = signal_volume (거래량 위반 판정 기준)
  'd_score': int,
  'price_break_first_at': None,  # -2% 시작 시각 (15초 지속 측정용)
  'task': asyncio.Task,
}
```

### 3.3 1차 풀 모니터링 (5분)

**즉시 트리거 — 가격 -2% 지속 15초**

[sector/pool_monitor.py:143-159 `_check_price_immediate`](../sector/pool_monitor.py)

0B push가 올 때마다 호출. 현재가가 `signal_price × 0.98` 이하면 `price_break_first_at` 기록 → 15초 지속 시 즉시 2차 풀로 이동 (`reason='price_drop_2pct'`).

회복하면 `price_break_first_at = None`으로 리셋.

**5분 만료 평가 — 3항목**

[sector/pool_monitor.py:189-220 `_evaluate_5min`](../sector/pool_monitor.py)

| 항목 | 위반 조건 | 위반 코드 |
|---|---|---|
| 가격 | 최근가 < `signal_price × 0.98` | `price_drop_2pct` |
| 체결강도 | 5분간 평균 strength < 110 | `weak_strength` (데이터 없으면 `no_strength_data`) |
| 거래량 | 마지막 1분 합계 < `volume_baseline × 0.5` | `volume_drop` |

- 모두 통과 → `_execute_buy` 호출
- 1개 이상 위반 → `_move_to_secondary(reason=first_violation)`

### 3.4 2차 풀 모니터링 (10분)

[sector/pool_monitor.py:225-313](../sector/pool_monitor.py)

- 진입 시 1차 task cancel → 2차 task 시작 (0B 등록 유지)
- 10분 후 `_evaluate_secondary` 호출 — 마지막 5분 데이터로 1차와 동일 항목 평가
- 모두 통과 → 매수, 위반 → 폐기 (로그만)

### 3.5 매수 결정

[sector/pool_monitor.py:318-354 `_execute_buy`](../sector/pool_monitor.py)

| 조건 | 동작 |
|---|---|
| `bought_count >= DAILY_BUY_LIMIT` (5) | 한도 도달 마킹, skip |
| `bought_count < TOP_N_INITIAL` (3) | 무조건 매수 |
| `TOP_N_INITIAL <= bought_count < DAILY_BUY_LIMIT` | 기존 매수 중 최저 `d_score` 초과 시만 매수 (같으면 skip) |

**주문**: [sector/pool_monitor.py:356-384 `_place_order`](../sector/pool_monitor.py)
1. `check_bid` (호가 조회, `api/check_bid.py` ka10004)
2. `buy_stock` (지정가 매수, `api/buy_stock.py` kt10000)
3. 수량 = `BUY_QUANTITY` (1주 고정)

### 3.6 일별 리셋

[sector/pool_monitor.py:438-465](../sector/pool_monitor.py)

- `start_daily_reset_loop()` — 09:00 KST 자동 reset task (싱글톤)
- `reset_daily_state()` — `_bought_today` 초기화, `_daily_limit_reached = False`

---

## 4. PoolBuffer — 0B 데이터 누적

**구현**: [sector/pool_buffer.py](../sector/pool_buffer.py)

**역할**: 0B push (가격/체결강도/거래량) 시계열을 메모리(deque, max 600개)에 누적. 30초 간격으로 디스크 flush (`logs/pool_buffer/buffer_YYYY-MM-DD.json`).

**핵심 API**:
- `append(code, price, strength, volume)` — 1건 추가 (sync)
- `get_history(code) -> {'price': [(ts, val), ...], 'strength': [...], 'volume': [...]}` — 조회
- `remove(code)` — 풀 종료 시 메모리 정리
- `maybe_flush()` — 30초 경과 시 자동 flush
- `flush()` — 강제 flush (tmp + replace)

**싱글톤**: `get_buffer()` — 봇 전역 1개.

---

## 5. WebSocket dispatch — 0B push → PoolMonitor

[automation/realtime/websocket.py:1908-1926 `_handle_stock_quote`](../automation/realtime/websocket.py)

```python
if hasattr(self, 'pool_monitor') and self.pool_monitor is not None:
    code = response.get('item', '')
    v = response.get('values', {})
    price = abs(float(v.get('10', '0')))      # FID 10  현재가
    strength = float(v.get('228', 0) or 0)    # FID 228 체결강도
    volume = abs(int(v.get('15', '0')))       # FID 15  거래량
    session = str(v.get('290', ''))           # FID 290 장구분 (2=정규장)
    if session == '2':
        self.pool_monitor.on_quote(code, price, strength, volume)
```

정규장(290=2)에서만 누적. 단순보호: feature 활성화와 무관하게 항상 dispatch.

---

## 6. D-score (캔들 품질) — 7일 일봉 분석

**구현**: [sector/candle_quality.py](../sector/candle_quality.py)

**함수**: `evaluate_candle_quality(bars_7d: pd.DataFrame) -> dict` — 7행 OHLCV → 0~10점.

| 항목 | 만점 | 평가 |
|---|---|---|
| D1. 주도 양봉 강도 | 2 | 최대 양봉이 +5% 이상이면, 윗꼬리 <20% (1점) + 몸통 >60% (1점) |
| D2. 주도 양봉 위치 | 1 | 주도 양봉이 2~4일 전이면 1점 (이상적 눌림 시간 확보) |
| D3. 눌림 깊이 | 2 | 주도일 고가 → 이후 최저가의 낙폭. 3~8%면 2점, 0~3% 또는 8~12%면 1점 |
| D4. 양봉 우세 | 1 | 7일 중 양봉 4개 이상 |
| D5. 평균 윗꼬리 | 1 | 양봉들 평균 윗꼬리 < 25% |
| D6. 당일 캔들 | 2 | 당일 양봉 + 종가 위치 ≥70% (1점) + 윗꼬리 <20% (1점) |
| D7. 거래량 패턴 | 1 | 주도일 거래량 급증(>1.5×) + 조정일 감소(<0.7×) + 당일 회복(>1.2×) |

**반환**:
```python
{'score': int(0~10), 'breakdown': {'D1':int, ..., 'D7':int}, ...}
```

---

## 7. 데이터 로더 — 7일 일봉 read

**구현**: [tools/data_loaders.py](../tools/data_loaders.py)

**핵심 함수**: `load_7d_bars(code, end_date=None) -> pd.DataFrame | None`

**경로**: `\\beelink\market_data\bars_1d\stocks\{code}\{YYYY}.parquet` (외부 데이터, market_data_collector가 생성)

**구현 포인트**:
- `code = str(code).zfill(6)` — 6자리 0-padded 정규화 (CSV에서 int로 추론된 경우 대비)
- 연초(1월 ≤14일)면 전년도 parquet도 읽어 7거래일 확보
- `date <= end_date` 필터 후 `.tail(7)` → 7행 OHLCV 반환
- 데이터 < 7행이면 `None`

**환경변수**: `MARKET_DATA_ROOT` (default `\\beelink\market_data`)

기타 함수:
- `load_today_pool_codes()` — 수집풀 코드 리스트
- `load_today_pool_full()` — 수집풀 dict (메타 포함)
- `lookup_close(code, eval_date, offset_bdays)` — N영업일 후 종가 (백필용)
- `load_market_change(eval_date)` — KOSPI(001)/KOSDAQ(101) 등락률 (분봉 첫open → 마지막close)
- `lookup_history(code, days)` — `candle_quality_daily/*.csv`에서 등장 이력

---

## 8. 진입점 — 두 WebSocket 핸들러

### 8.1 CNSRREQ 핸들러 (초기 스냅샷)

[automation/realtime/websocket.py:365-405](../automation/realtime/websocket.py)

`start real 1` 호출 시 `CNSRREQ`(`search_type='1'`) 요청을 보내면 키움이 **현재 조건 만족 종목의 초기 스냅샷**을 응답.

```python
elif trnm == 'CNSRREQ':
    if return_code == 0:
        data = response.get('data', [])
        stock_codes = []
        for item in data:
            code = item.get('jmcode')              # PDF ka10173 스펙
            if not code and 'values' in item:
                code = item['values'].get('9001')   # fallback
            code = str(code).lstrip('A').strip()    # 'A005930' → '005930'
            stock_codes.append(code)
        for code in stock_codes:
            if code in self.processing_stocks:
                continue
            self.processing_stocks.add(code)
            asyncio.create_task(self._safe_add_to_pool(code, seq_id=seq_str))
            asyncio.create_task(pool_evaluate_and_add(code))
```

### 8.2 REAL 핸들러 (실시간 신규 매칭)

[automation/realtime/websocket.py:481-506](../automation/realtime/websocket.py)

매칭이 신규로 발생할 때마다 REAL 메시지 도착. `item['values']['9001']`에서 종목 코드 추출, `event_type == 'I'`(편입)인 경우만 처리.

두 핸들러 모두 동일 함수 호출:
- `self._safe_add_to_pool(code, seq_id=...)` → `collection_pool.json`에 적재
- `pool_evaluate_and_add(code)` → 7일봉 read → D-score → 임계 통과 시 1차 풀

`_safe_add_to_pool` (websocket.py:665-677)는 `processing_stocks` 락을 `finally`에서 반드시 해제하는 안전 래퍼.

### 8.3 모듈 레벨 진입점

[sector/pool_monitor.py:483-508 `evaluate_and_add(code)`](../sector/pool_monitor.py)

```python
async def evaluate_and_add(code):
    monitor = get_monitor()
    if monitor is None: return
    bars = load_7d_bars(code)
    if bars is None or len(bars) < 7: return
    r = evaluate_candle_quality(bars)
    d_score = int(r['score'])
    if d_score < D_SCORE_MIN: return
    signal_price = float(bars.iloc[-1]['close'])
    signal_volume = int(bars.iloc[-1]['volume'])
    await monitor.add_to_pool(code, signal_price, signal_volume, d_score)
```

---

## 9. 봇 통합 — PoolMonitor 부착

[automation/telegram/chat_command.py `__init__`](../automation/telegram/chat_command.py) (검색: `set_monitor`)

```python
from sector.pool_monitor import PoolMonitor, set_monitor
self.pool_monitor = PoolMonitor(self.websocket)
set_monitor(self.pool_monitor)
self.websocket.pool_monitor = self.pool_monitor

from core.daily_task import DailyTaskManager
self.daily_task = DailyTaskManager(self)
```

[automation/core/main.py `run` / `shutdown`](../automation/core/main.py):
- `run()`: `daily_task.start()` + 시작 시 `collection_pool` clear
- `shutdown()`: `daily_task.stop()`

---

## 10. 일별 후처리 — DailyTaskManager (16:30 KST)

**구현**: [automation/core/daily_task.py](../automation/core/daily_task.py)

봇 내부 30초 주기 스케줄러 (외부 프로세스 사용 금지 — `feedback_no_external_data_mutation` 영구 원칙).

**4단계 (순서 보장)**:
1. `evaluate_today_pool()` — 오늘 수집풀 종목 D-score 평가 → `candle_quality_daily/YYYY-MM-DD.csv` 저장 (수집풀 빈 경우 skip)
2. `backfill_returns()` — 과거 CSV들에 N일 후 수익률(BD+1, +3, +5, +10) 채움
3. `rebuild_master()` — 전체 CSV 통합 → `candle_quality_master.csv`
4. `clear_pool()` — `collection_pool.json` 비우기

**관련 모듈**: [tools/daily_quality_logger.py](../tools/daily_quality_logger.py)
- 빈 풀이면 CSV 생성 안 함
- `pd.read_csv(..., dtype={'code': str})` 강제 (code 컬럼 int 추론 방지)
- `EmptyDataError` 캐치

---

## 11. 운영 환경 / 좋은 점검 흔적

- **운영기**: Beelink (`C:\Kiwoom_RtoB`, 24/7 데몬, Task Scheduler `KiwoomRtoB_Bot`)
- **개발기**: PC (`D:\Kiwoom_RtoB`, dev only)
- **운영 흐름**: PC에서 commit/push → Beelink에서 git pull → bot 재시작 (`schtasks /End` + `/Run`)
- **재시작 시 확인**: bot.log tail에서 `DailyTaskManager started` + `[startup] collection_pool cleared` 두 줄
- **로그**: `C:\Kiwoom_RtoB\logs\bot.log` (UTF-8, cmd wrapper가 stdout 리다이렉트)
- **0B subscription 한도**: 95개, 80%/95% 경고 — `_count_active_subscriptions`는 `ws.registered_items` 기준
- **수동 확인 도구**: `watch/watch_pool.bat`, `watch_errors.bat`, `watch_daily.bat`, `watch_log.bat` (SSH tail with PowerShell `[Console]::OutputEncoding=UTF8`)

---

## 12. 주의사항 (디버깅 시 확인)

1. **수집풀 비어 보임**: CNSRREQ 핸들러 누락 케이스. 종목 코드 추출이 `jmcode` (PDF) vs `values.9001` (구버전) 둘 다 처리하는지 확인.
2. **1차 풀 진입 안 됨**: D-score 평가에서 7행 못 채웠을 수 있음. `\\beelink\market_data\bars_1d\stocks\{code}\2026.parquet` 존재 확인.
3. **D-score 통과인데 풀 안 들어감**: 이미 보유 중이거나, 0B 등록 95% 초과, 또는 일별 매수 한도 도달.
4. **거래량 위반 자주 발생**: `volume_baseline = signal_volume` (조건 매칭 시점 일봉 거래량). 장중 1분 거래량과 비교라 baseline이 비현실적으로 클 수 있음 — 모니터링 필요.
5. **2차 풀 진입 후 0B 재등록 실패**: 한도 초과 시. `_register_quote`가 `RuntimeError` raise → `_secondary.pop` 후 종료.
6. **외부 프로세스 데이터 수정 금지**: `collection_pool.json` 등 봇 상태 파일은 봇 데몬 내부에서만 변경. `clear_pool` 외부 호출 절대 금지.
