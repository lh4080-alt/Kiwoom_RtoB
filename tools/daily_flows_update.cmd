@echo off
rem 04:40 매일 — 시장 단위(ka10051) 후 3자 종목(ka10059) 증분 갱신. 브리프 05:35 전 완료 목적.
cd /d C:\Kiwoom_RtoB
set PYTHONIOENCODING=utf-8
python -X utf8 toolsackfill_market_flows_k51.py --update >> automation\logslows_update.log 2>&1
python -X utf8 toolsackfill_flows3_long.py --update >> automation\logslows_update.log 2>&1
