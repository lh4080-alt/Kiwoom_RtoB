# Archive

D-score / PoolMonitor 시스템 폐기에 따라 보관된 문서들 (2026-05-19).

폐기 사유: 봇 재설계 — 장중 실시간 매수 결정 → 장 마감 후 분석 + 다음날 09:00 매수로 전환.

폐기된 모듈:
- `sector/pool_monitor.py` (1차/2차 풀)
- `sector/pool_buffer.py` (0B push 누적 버퍼)
- `sector/candle_quality.py` (D-score 평가)
- `tools/daily_quality_logger.py` (CSV 후처리)

대체 예정 모듈 (재설계 Part 2):
- `automation/modules/daily_analyzer.py` (16:00 자동 분석 + 텔레그램 알림)
- `automation/modules/buy_executor.py` (09:00 매수 + 다층 방어)
- `automation/modules/holdings_manager.py` (보유 모니터링 + 자동 매도)
- `automation/modules/condition_collector.py` (조건검색 매칭 수집)
