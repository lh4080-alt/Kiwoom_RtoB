# cond_verify — 조건검색식 엣지 검증 (Phase 1 골격)

조건검색식 cond0~3 (봇 search_seq=[0,1,2,3])이 **정말 엣지 있는 종목을 뽑는지** forward-return으로 검증.

## 데이터 (이미 봇이 수집 중 — 새 수집 파이프라인 불필요)
- **수집 기록**: `../candle_quality_daily/*.csv` — `eval_date, code, seq_ids(어느 cond), score, today_close, kospi_chg, kosdaq_chg` (봇 16:30 로거가 매일 기록).
- **가격**: `\\beelink\market_data\bars_1d\stocks\{code}\{YYYY}.parquet` (OHLCV).

## 측정 (cond 순수 엣지 — 봇 실제 매수와 분리)
표준 forward-return: **익일(D+1) 시가 매수 → D+1/D+3/D+5 종가 매도** 수익률.
- cond(seq)별 PF / 교집합 강도(strength)별 PF / 보유기간(1/3/5일)별 PF.
- PF = Σ수익 / |Σ손실|. >1.5 우수, >2.0 매우우수, <1.0 손실.

## 실행 (BEELINK에서 — 데이터가 거기 있음)
```
python cond_verify/analyze_cond_pf.py
```
- 데이터가 며칠 쌓여야 의미 있음 (forward-return은 시간 경과 필요). 주간 실행 권장.
- 가설 H1~H5는 cond_spec.json 참조.

## 영역 / 한계
- cond **정의·수정**은 조건검색 설계 Claude(`d:\Kiwoom_search`) 담당. 여기는 봇 수집·분석만.
- **수집 모델 주의**: 봇은 장중 실시간 매칭 수집 → "D일 종가 기준 스냅샷"과 미세 차이. 정밀 검증은 Phase 2에서 15:30~16:00 ka10172 EOD 스냅샷 추가.
- 교집합 강도는 candle_quality CSV의 seq_ids에 의존 (한 종목이 여러 cond에 잡힌 게 다 기록돼야 정확).

## TODO (Phase 2+)
- ka10172 EOD 스냅샷 수집 (종가 기준 정확 매칭)
- 시장 baseline(KOSPI/KOSDAQ forward) 대비 초과수익
- 갭컷 시뮬 / 슬리피지·수수료 / 주간·월간 보고서 자동화
