# -*- coding: utf-8 -*-
"""semi_trigger 설정 — 모든 임계값·옵션의 단일 위치 (지시서 완료 조건).

원칙: 결과를 본 뒤 이곳을 바꾸면 새 버전으로 기록 (지시서 §8 파라미터 버전 관리).
버전: 2026-10-09 v1 (지시서 작업 1~7 초기값)
"""

# ── 작업 3: 트리거 이원화 ────────────────────────────────────
Z_TH = -1.3            # z 기준 (기존 -1.0에서 이원화 도입과 함께 완화 — RET_TH가 보완)
RET_TH = -3.5          # 절대낙폭 기준 (% — 누적 미국 세션 수익률)
SIGMA_WINDOW = 20      # σ 계산 윈도 (거래일, 60 옵션 — 로그에 두 값 병기)

# ── 작업 4: us_memory 분리 + legacy 결합 ─────────────────────
US_MEM_COMPOSITION = {
    'mem_dram': ['MU'],          # 판정 포함
    'mem_nand': ['SNDK'],        # 판정 포함
    'mem_hdd': ['WDC', 'STX'],   # 알림 표시 전용 (config로 포함 여부 선택)
}
HDD_INCLUDE_IN_SIGNAL = False
# 종목별 기본 가중치 (가중치 산출 스크립트 결과로 갱신 — tools/us_weight_calc.py)
STOCK_US_WEIGHTS = {
    '000660': {'MU': 0.73, 'SNDK': 0.27},  # v3 다변량 재산출 (2026-10-09, us_weight_v2.json)
    '005930': {'MU': 0.59, 'SNDK': 0.41},  # v3 다변량 재산출 (2026-10-09)
}
US_WEIGHT_MODE = 'fixed'   # 'fixed' | 'rolling_beta' (60일 롤링 β 기반 가중 — v3 옵션)
LEGACY_MODE = 'min'    # 판정용 반도체 신호 = min(us_mem_z, legacy_z) | 'mean'

# 하닉 트리거 모드 (2026-10-09 Lee 지시 — FDR 통과 확인 후 전환)
# 'us_drop': 기존 MU 하락 트리거 | 'residual_gap': MU로 설명 안 되는 과대 갭
# (잔차 <= -1.5σ, 판정은 한국 시가 확정 후 09:01 → 09:05 알림)
HYNIX_TRIGGER_MODE = 'us_drop'

# ── 작업 5: 수급 역행 경고 ───────────────────────────────────
SUPPLY_WARN = {
    'program_z': -0.5,       # 프로그램 순매수 z <= 이 값
    'foreign5d_lt': 0.0,     # 외인 5일 누적 < 0 이고
    'foreign_z': -0.5,       # 외인 z <= 이 값
    'volratio_z': 1.0,       # 주가 하락 + 거래량 변화율 z >= 이 값
    'min_hits': 2,           # 충족 개수 이상이면 경고
}

# ── 작업 6: 60일선 완충 구간 ─────────────────────────────────
MA60_WARN_BAND = -2.0   # 괴리율 -2%~0% 경계 (하향 제외선), >=0% 통과

# ── 발송 규칙 (작업 1) ───────────────────────────────────────
HEADER_FMT = '[semi_trigger] US {us_date} 세션 → KR 실행 {kr_date} (발송 {sent_at})'
