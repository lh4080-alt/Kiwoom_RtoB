# 지시서: git 초기화 + Beelink 동기화 + 일일 누적 검증 시스템

## 전체 구성
- **Part A**: git 초기화 + GitHub push + Beelink 동기화 (오늘 즉시)
- **Part B**: 일일 누적 + 사후 검증 시스템 (5~7거래일 운영)
- **Part C**: 30개 시점 종합 분석 (스켈레톤)

---

# Part A. git 초기화 + GitHub push + Beelink 동기화

## A1. 사전 점검 (데스크탑)

```powershell
cd D:\Kiwoom_RtoB
git status  # 이미 git repo인지 확인
```

이미 git repo면 A2 건너뛰고 A3부터.

## A2. .gitignore 작성

**경로:** `D:\Kiwoom_RtoB\.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
.venv/
venv/
env/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# 데이터 (절대 push 금지)
bar_storage/
waiting_pool.db*
*.parquet
*.sqlite
*.sqlite-journal
*.db
*.db-wal
*.db-shm

# 로그
logs/
*.log

# 시크릿
.env
config/secrets.yml
config/secrets.yaml
*token*
*key*

# 분포 결과 CSV (재현 가능, repo 부풀림 방지)
candle_quality_distribution.csv
candle_quality_daily/
candle_quality_master.csv

# 임시 파일
tmp/
temp/
*.bak
```

## A3. git 초기화 + 커밋

```powershell
cd D:\Kiwoom_RtoB

# 신규 repo인 경우
git init
git branch -M main

# 첫 커밋
git add .
git status  # 추가될 파일 검토 — 시크릿/데이터 포함 여부 반드시 확인
git commit -m "Initial commit: kiwoom_RtoB bot baseline"
```

**검토 포인트 (커밋 전 필수):**
- `git status` 출력에 `*.db`, `*.parquet`, `token`, `secret` 단어 없는지 확인
- 발견 시 즉시 .gitignore 수정 후 `git rm --cached <파일>` 처리

## A4. GitHub repo 생성 + push

GitHub 웹에서 신규 repo 생성:
- 이름: `kiwoom_RtoB`
- Private (필수)
- README/`.gitignore`/LICENSE 추가 옵션 모두 **체크 해제**

```powershell
cd D:\Kiwoom_RtoB
git remote add origin git@github.com:lh4080-alt/kiwoom_RtoB.git
git push -u origin main
```

## A5. Beelink에서 clone

```bash
# Beelink SSH 접속 후
cd C:/
git clone git@github.com:lh4080-alt/kiwoom_RtoB.git
cd kiwoom_RtoB

# 또는 PowerShell이면
cd C:\
git clone git@github.com:lh4080-alt/kiwoom_RtoB.git
cd C:\kiwoom_RtoB
```

## A6. Beelink 환경 셋업

```powershell
cd C:\kiwoom_RtoB

# 가상환경 (Python 64-bit, 기존 kiwoom_VB_v2와 동일 버전 사용)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 시크릿 수동 복사 (gitignore로 push 안 됨)
# - 데스크탑의 config/secrets.yml → Beelink 동일 경로로 복사
# - .env 등도 동일

# 데이터 디렉토리 생성 (gitignore로 push 안 됨)
mkdir bar_storage
mkdir logs
mkdir candle_quality_daily
```

## A7. 검증

```powershell
# Beelink에서
cd C:\kiwoom_RtoB
python -c "from sector.candle_quality import evaluate_candle_quality; print('OK')"
```

import 성공하면 A 완료.

---

# Part B. 일일 누적 + 사후 검증 시스템

## B1. 모듈 파일

**경로:** `kiwoom_RtoB/tools/daily_quality_logger.py`

```python
"""
매일 장 마감 후 실행.
1. 오늘 수집풀 종목들의 D 점수 평가
2. 시장 상태 기록
3. 어제 평가한 종목들의 익일 수익률 사후 기록
4. 마스터 CSV에 누적
"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from sector.candle_quality import evaluate_candle_quality
# bar_storage / 수집풀 / 시장지수 로더는 기존 모듈에서 import


DAILY_DIR = Path('candle_quality_daily')
MASTER_CSV = Path('candle_quality_master.csv')


def evaluate_today_pool(eval_date: str) -> pd.DataFrame:
    """오늘 수집풀 종목 평가 + 시장 상태 기록."""
    codes = load_today_pool_codes()  # 수집풀 로더
    kospi_chg, kosdaq_chg = load_market_change(eval_date)  # 시장 등락률
    
    results = []
    for code in codes:
        bars = load_7d_bars(code, end_date=eval_date)
        if bars is None or len(bars) < 7:
            continue
        r = evaluate_candle_quality(bars)
        today_close = bars.iloc[-1]['close']
        
        row = {
            'eval_date': eval_date,
            'code': code,
            'today_close': today_close,
            'score': r['score'],
            'pullback_pct': r['pullback_depth_pct'],
            'bullish_ratio': r['bullish_ratio'],
            'avg_wick': r['avg_upper_wick'],
            'kospi_chg': kospi_chg,
            'kosdaq_chg': kosdaq_chg,
            'eval_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            # 사후 검증용 (다음 거래일에 채워짐)
            'd1_close': None,
            'd1_return_pct': None,
            'd5_close': None,
            'd5_return_pct': None,
        }
        row.update(r['breakdown'])
        results.append(row)
    
    df = pd.DataFrame(results)
    daily_path = DAILY_DIR / f'{eval_date}.csv'
    df.to_csv(daily_path, index=False)
    print(f"[일일 평가] {eval_date}: {len(df)}건 저장 → {daily_path}")
    return df


def backfill_returns(eval_date: str):
    """
    eval_date의 1거래일 후 / 5거래일 후 수익률을 사후 기록.
    매일 실행 시 과거 7일치 데이터의 미채움 컬럼을 채움.
    """
    cutoff = datetime.strptime(eval_date, '%Y-%m-%d') - timedelta(days=10)
    
    for daily_file in DAILY_DIR.glob('*.csv'):
        file_date = datetime.strptime(daily_file.stem, '%Y-%m-%d')
        if file_date < cutoff:
            continue
        
        df = pd.read_csv(daily_file)
        updated = False
        
        for idx, row in df.iterrows():
            if pd.isna(row['d1_return_pct']):
                d1_close = lookup_close(row['code'], row['eval_date'], offset_bdays=1)
                if d1_close is not None:
                    df.at[idx, 'd1_close'] = d1_close
                    df.at[idx, 'd1_return_pct'] = (d1_close - row['today_close']) / row['today_close'] * 100
                    updated = True
            
            if pd.isna(row['d5_return_pct']):
                d5_close = lookup_close(row['code'], row['eval_date'], offset_bdays=5)
                if d5_close is not None:
                    df.at[idx, 'd5_close'] = d5_close
                    df.at[idx, 'd5_return_pct'] = (d5_close - row['today_close']) / row['today_close'] * 100
                    updated = True
        
        if updated:
            df.to_csv(daily_file, index=False)
            print(f"[사후 기록] {daily_file.stem}: 수익률 보충")


def rebuild_master():
    """일일 CSV들을 마스터 CSV로 통합."""
    dfs = []
    for daily_file in sorted(DAILY_DIR.glob('*.csv')):
        dfs.append(pd.read_csv(daily_file))
    if dfs:
        master = pd.concat(dfs, ignore_index=True)
        master.to_csv(MASTER_CSV, index=False)
        print(f"[마스터] {len(master)}건 → {MASTER_CSV}")
        return master
    return pd.DataFrame()


def lookup_close(code: str, eval_date: str, offset_bdays: int) -> float | None:
    """
    eval_date 기준 offset_bdays 영업일 후의 종가.
    아직 도래 안 했으면 None.
    """
    target_date = pd.to_datetime(eval_date) + pd.tseries.offsets.BDay(offset_bdays)
    if target_date > pd.Timestamp.now().normalize():
        return None
    bars = load_7d_bars(code, end_date=target_date.strftime('%Y-%m-%d'))
    if bars is None or len(bars) == 0:
        return None
    last = bars.iloc[-1]
    if pd.to_datetime(last['date']).normalize() == target_date.normalize():
        return last['close']
    return None


def load_market_change(eval_date: str) -> tuple:
    """KOSPI/KOSDAQ 당일 등락률 반환. 기존 시장지수 모듈에 맞춰 연결."""
    raise NotImplementedError('시장지수 로더 연결 필요')


if __name__ == '__main__':
    DAILY_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 1. 오늘 평가
    evaluate_today_pool(today)
    
    # 2. 과거 데이터 수익률 채우기
    backfill_returns(today)
    
    # 3. 마스터 재구성
    rebuild_master()
```

## B2. 스케줄 등록 (Beelink)

```powershell
# Windows Task Scheduler 등록
# 작업 이름: KiwoomRtoB_DailyQualityLogger
# 트리거: 매일 16:30 (장 마감 + 데이터 안정화 대기)
# 동작: python C:\kiwoom_RtoB\tools\daily_quality_logger.py
# 작업 디렉토리: C:\kiwoom_RtoB
```

`tools/register_daily_logger_task.ps1` 작성 (Administrator 권한으로 실행):

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\kiwoom_RtoB\.venv\Scripts\python.exe" `
    -Argument "C:\kiwoom_RtoB\tools\daily_quality_logger.py" `
    -WorkingDirectory "C:\kiwoom_RtoB"

$trigger = New-ScheduledTaskTrigger -Daily -At "16:30"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "KiwoomRtoB_DailyQualityLogger" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Daily candle quality D-score logger"
```

## B3. 동작 검증

등록 직후 수동 실행:

```powershell
cd C:\kiwoom_RtoB
.venv\Scripts\activate
python tools\daily_quality_logger.py
```

출력 확인:
- 일일 CSV 생성 (`candle_quality_daily/YYYY-MM-DD.csv`)
- 마스터 CSV 생성
- 에러 없음

## B4. 로그 + Telegram 알림 (선택)

기존 Telegram 봇 인프라가 있으면 일일 실행 결과 요약 전송:

```
[D-Score Logger] 2026-05-XX
신규 평가: N건
누적 표본: M건
미채움 수익률: K건
오류: 0
```

---

# Part C. 30개 시점 종합 분석 (스켈레톤)

표본 30개+ 누적된 시점에 별도 지시서로 디테일 채움. 지금은 분석 항목만 정의.

## C1. 분석 시점 트리거

- 누적 표본 ≥ 30건
- 또는 5거래일 경과 (둘 중 빠른 쪽)

## C2. 분석 모듈 (예정)

**경로:** `kiwoom_RtoB/tools/analyze_master.py` (지금은 작성 안 함)

다음을 수행할 예정:

### (a) 분포 진단
- 전체 점수 히스토그램
- 항목별 평균/통과율
- bimodal 여부 재검증
- 이상치(극단값) 식별

### (b) 사후 수익률 상관관계 (핵심)
- 점수 구간별 d1, d5 평균/중간값/표준편차
- 상관계수 (점수 vs d1 수익률, score vs d5 수익률)
- 점수 7+ vs 0~2 구간 평균 수익률 차이의 통계적 유의성 (t-test)

### (c) 항목별 개별 효력
- 각 D1~D7이 단독으로 수익률 예측에 기여하는가
- 다중회귀로 항목별 계수 추정
- 계수가 0에 가깝거나 음수인 항목 → 제거 또는 재설계 대상

### (d) 시장 상태 보정
- KOSPI/KOSDAQ 등락률을 통제했을 때 D 점수의 잔여 설명력
- 시장 약세일에 D6이 0점이 되는 게 의도된 거면 → 시장 보정 점수 도출
- 시장 강세에 거의 모든 종목이 고득점이면 → 알파 없음

### (e) 임계 재조정 후보
- 분석 결과에 따라 항목별 임계 조정
- 예: D7 임계 1.5/0.7/1.2 → 통계 기반 재산정

## C3. 출력 결과물 (예정)

- `analysis_report.md` (Markdown 리포트)
- `correlation_matrix.png`
- `score_vs_return_scatter.png`
- 항목별 회귀 계수 표

## C4. 의사결정 트리

```
분석 결과 → 다음 중 1개 선택
A. 현 점수 체계 유지 → score.py에 D 통합, 가중치 확정
B. 항목 추가/제거 → 모듈 v3 작성
C. 임계 조정만 → 임계만 수정 후 추가 누적
D. 표본 더 필요 → C 시점을 60건 / 100건으로 연기
```

---

# 전체 작업 순서

```
[오늘]
1. Part A 전체 수행 (git 초기화 → push → Beelink clone → 환경셋업)
2. Part B1, B2 수행 (모듈 + 스케줄 등록)
3. Part B3 검증 (수동 실행 1회)

[매일 자동]
- 16:30 KiwoomRtoB_DailyQualityLogger 실행
- candle_quality_master.csv 누적

[5~7거래일 후 또는 표본 30+ 시점]
- Part C 지시서 신규 작성 요청
- analyze_master.py 작성 + 실행
```

---

# 주의사항

- **시크릿 파일은 절대 git push 금지** — .gitignore 확실히 확인
- **데이터 디렉토리 push 금지** — bar_storage, *.db 등 .gitignore 처리
- **Beelink는 read-write로 운영** — 일일 CSV 누적이 여기서 발생
- **데스크탑은 개발용** — Beelink로 코드 변경 시 push → pull 4단계 워크플로우 준수
  ```
  데스크탑: git commit → git push origin
  Beelink:  ssh 접속 → cd C:\kiwoom_RtoB → git pull
  ```
- **데이터 동기화는 별도** — 일일 CSV는 Beelink에서만 생성됨. 데스크탑에서 분석하려면 별도 동기화 방법 필요 (rsync, 네트워크 공유, 또는 Beelink에서 직접 분석)
