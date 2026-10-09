# -*- coding: utf-8 -*-
"""일일 브리프 설정 — 모든 임계값·가중치·발송 스위치의 단일 위치.

원칙: 결과를 본 뒤 이곳을 바꾸면 새 버전으로 기록 (지시서 §8 파라미터 버전 관리).
semi_trigger의 semi_config.py는 이 파일을 그대로 다시 내보낸다 (값 중복 정의 금지).

버전 이력
  2026-10-09 v1  semi_config v1 이관 + 브리프 신규 항목 (예상 갭·적립식·발송 스위치)
"""

# ── 발송 스위치 ───────────────────────────────────────────────
BRIEF_SEND = False           # 통합 브리프 발송 — 5거래일 병행 diff 0 확인 후 True
LEGACY_MACRO_SEND = True     # 기존 거시 모니터 16:50 개별 발송 (병행 기간 유지)
LEGACY_SEMI_SEND = True      # 기존 semi_trigger 05:30 개별 발송 (병행 기간 유지)
# 발송 시각은 하드코딩하지 않음 — 매 미국 세션 마감(16:00 America/New_York, zoneinfo로
# 서머타임 자동) + 아래 지연. 예비/확정은 실행일 전 남은 미국 세션 유무로 헤더에 표기.
BRIEF_SEND_DELAY_MIN = 30
# NYSE 휴장일 (연 단위 하드코딩 — KR_HOLIDAYS와 같은 방식)
US_HOLIDAYS = {
    '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19',
    '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25',
    '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18',
    '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24',
}
FOREIGN_Z_EXTREME = 2.0      # 외인 z20/z60 중 |z| ≥ 이 값이면 '(극단)'
AD_ORIGIN = '2025-01-02'     # A/D 누적 기준점 (패널 첫 거래일) — 바꾸면 누적값 전체가 이동

# ── 트리거 (semi_trigger 작업 3) ─────────────────────────────
Z_TH = -1.3            # 가중 z 기준
RET_TH = -3.5          # 가중 누적 미국 수익률 기준 (%)
SIGMA_WINDOW = 20      # σ 계산 윈도 (거래일, 60 옵션)

# ── us_memory 구성·가중치 (작업 4) ───────────────────────────
US_MEM_COMPOSITION = {
    'mem_dram': ['MU'],          # 판정 포함
    'mem_nand': ['SNDK'],        # 판정 포함
    'mem_hdd': ['WDC', 'STX'],   # 표시 전용
}
HDD_INCLUDE_IN_SIGNAL = False
STOCK_US_WEIGHTS = {
    '000660': {'MU': 0.73, 'SNDK': 0.27},  # 다변량 재산출 2026-10-09 (us_weight_v2.json)
    '005930': {'MU': 0.59, 'SNDK': 0.41},
}
US_WEIGHT_MODE = 'fixed'     # 'fixed' | 'rolling_beta'
LEGACY_MODE = 'min'          # min(us_mem_z, legacy_z) | 'mean'
HYNIX_TRIGGER_MODE = 'us_drop'   # 'us_drop' | 'residual_gap' (FDR 통과 시 전환)

# ── 수급 역행·MA60 (표시 전용 — 등급 스위치 OFF) ─────────────
SUPPLY_WARN = {
    'program_z': -0.5,
    'foreign5d_lt': 0.0,
    'foreign_z': -0.5,
    'volratio_z': 1.0,
    'min_hits': 2,
}
MA60_WARN_BAND = -2.0
SUPPLY_GRADE_SWITCH = False
MA60_GRADE_SWITCH = False

# ── 예상 갭 (브리프 작업 3) ──────────────────────────────────
GAP_BETA_WINDOW = 60         # 롤링 β 창 (KR 실행일 기준, 당일 미포함)
GAP_MIN_OBS = 40             # β 산출 최소 관측 수 — 미만이면 예상 갭 미표시

# ── 적립식 연동 (브리프 작업 4) ──────────────────────────────
DCA_DAY = 'THU'              # 정기 매수 요일 (MON~FRI) — 휴장이면 다음 거래일
DCA_FREQ = 'weekly'          # 'weekly' | 'biweekly' | 'monthly'
DCA_ANCHOR = '2026-01-01'    # biweekly 주기 기준일
DCA_PULL_FORWARD_STOCKS = ('005930',)   # 회차 당김 적용 종목 (하닉은 정기 일정만)
DCA_REGIME_SCALING = False   # 국면별 비중 조정 (기본 OFF)
DCA_REGIME_MULT = {'강세장': 1.0, '보합·전환기': 1.0, '하락장': 1.0}  # 조정 ON 시 배수

# ── 비용 ─────────────────────────────────────────────────────
COST_RT = 0.0025             # 왕복 거래비용 (수수료+세금+슬리피지)
COST_BUY = 0.00015           # 매수 1회 비용 (수수료+슬리피지, 세금 없음) — 적립식 단가 비교용

# ── 무결성 검사 (V4) ─────────────────────────────────────────
RANGE_LIMITS = {
    'daily_ret_abs_max': 30.0,   # 일간 수익률 |r| (%)
    'rate_min': 0.0, 'rate_max': 15.0,    # 금리 (%)
    'usdkrw_min': 800.0, 'usdkrw_max': 2000.0,
}
STALE_WARN_DAYS = 3          # 결측 시 직전값 사용 — 이 일수 이상이면 별도 경고

# ── 표기 ─────────────────────────────────────────────────────
HEADER_FMT = '[semi_trigger] US {us_date} 세션 → KR 실행 {kr_date} (발송 {sent_at})'
