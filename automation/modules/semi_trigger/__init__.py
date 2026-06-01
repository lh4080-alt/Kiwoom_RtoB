"""반도체 단일ETF 다요인 트리거 점수 시스템 (semi_trigger).

5축 z-score 가중합 기반 매수 트리거 (삼성전자/SK하이닉스 단일종목 레버리지 ETF):
  ① 미 메모리 강도 (MU·WDC·SNDK·STX 가중평균) — 40%
  ② 단일ETF 자금흐름 (14종 거래대금 합산) — 20%
  ③ 원/달러 변화 — 20%
  ④ 외인 5일 누적 수급 — 10%
  ⑤ 메모리 가격 (DDR) — 10%

semi_score = 0.40·us_memory_z + 0.20·etf_flow_z + 0.20·fx_z
           + 0.10·foreign_flow_z + 0.10·memory_price_z

원칙: AI 없음 / 자체 완결 (외부 봇 산출물 미의존) / shadow → 백테스트(OOS) → 트리거 교체.
영구 원칙 #30: 모든 키움 호출·상태변경은 봇 데몬 내부에서.
"""
