"""
키움 API 응답 구조 탐사 도구.

향후 새 API를 daily_analyzer 등에 통합하기 전에 endpoint/body/응답키/단위를
이 스크립트로 먼저 검증한다.

사용:
    cd d:\\Kiwoom_RtoB
    python tools\\api_probe.py

종목: 005930 (삼성전자 — 시계열 데이터가 안정적으로 채워짐)
검증 항목:
  - ka10046 체결강도: endpoint /mrkcond 검증 완료 (probe_api.py 결과)
  - ka10059 외인기관: endpoint 후보 stkinfo/mrkcond × date 유무 (응답키 stk_invsr_orgn, 시계열 정렬)
  - ka90013 프로그램매매: endpoint /mrkcond × amt_qty_tp 1/2 (응답키 stk_daly_prm_trde_trnsn, 단위)
"""
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'automation'))

PROBE_STK = '005930'


HOST_URL = 'https://api.kiwoom.com'  # 실계좌 (search 계정도 동일 host)


async def call(api_id, body, token, endpoint):
	"""공통 호출. (status, response_json) 반환."""
	from utils.rate_limiter import requests

	headers = {
		'Content-Type': 'application/json;charset=UTF-8',
		'authorization': f'Bearer {token}',
		'cont-yn': 'N',
		'next-key': '',
		'api-id': api_id,
	}
	r = await requests.post(HOST_URL + endpoint, headers=headers, json=body)
	try:
		data = r.json()
	except Exception:
		data = {'_raw': r.text}
	return r.status_code, data


async def _issue_search_token():
	"""search 계정 (config/search_*.txt) 키로 토큰 발급. real 계정 영향 없음."""
	from utils.rate_limiter import requests

	with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'search_app_key.txt'), encoding='utf-8') as f:
		app_key = f.read().strip()
	with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'search_app_secret.txt'), encoding='utf-8') as f:
		app_secret = f.read().strip()
	print(f'search key len={len(app_key)}, secret len={len(app_secret)}')

	url = HOST_URL + '/oauth2/token'
	headers = {'Content-Type': 'application/json;charset=UTF-8'}
	# 1차 시도: 입력 순서대로 (key→app_key, secret→app_secret)
	body = {'grant_type': 'client_credentials', 'appkey': app_key, 'secretkey': app_secret}
	r = await requests.post(url, headers=headers, json=body)
	d = r.json()
	token = d.get('token')
	if token:
		print(f'token OK (try1): expires={d.get("expires_dt")}')
		return token
	print(f'try1 fail: rc={d.get("return_code")} msg={d.get("return_msg")}')
	# 2차 시도: 위치 swap
	body2 = {'grant_type': 'client_credentials', 'appkey': app_secret, 'secretkey': app_key}
	r2 = await requests.post(url, headers=headers, json=body2)
	d2 = r2.json()
	token2 = d2.get('token')
	if token2:
		print(f'token OK (try2 swapped): expires={d2.get("expires_dt")}')
		print('NOTE: search_app_key.txt 와 search_app_secret.txt 내용이 뒤바뀐 상태로 사용됨 — 정정 필요')
		return token2
	print(f'try2 fail: rc={d2.get("return_code")} msg={d2.get("return_msg")}')
	return None


def dump(label, status, data, list_preview=3):
	"""응답 요약 출력. 리스트 키는 첫 N개 행을 보여줌."""
	print(f'\n=== {label} ===')
	print(f'status={status}, return_code={data.get("return_code")}, msg={data.get("return_msg")}')
	keys = [k for k in data.keys() if k not in ('return_code', 'return_msg')]
	print(f'top-level keys: {keys}')
	for k, v in data.items():
		if k in ('return_code', 'return_msg'):
			continue
		if isinstance(v, list):
			print(f'  {k} (list, count={len(v)})')
			for i, row in enumerate(v[:list_preview]):
				print(f'  [{i}] {json.dumps(row, ensure_ascii=False)}')
		elif isinstance(v, dict):
			print(f'  {k} (dict, keys={list(v.keys())[:10]})')
		else:
			print(f'  {k}={v}')


async def probe_ka10046(token):
	"""체결강도. probe_api.py에서 /mrkcond 검증된 상태 — 응답키 재확인."""
	print('\n' + '=' * 60)
	print('ka10046 체결강도 (검증)')
	print('=' * 60)
	for ep in ['/api/dostk/mrkcond', '/api/dostk/stkinfo']:
		s, d = await call('ka10046', {'stk_cd': PROBE_STK}, token, ep)
		dump(f'ka10046 via {ep}', s, d)
		if d.get('return_code') == 0:
			break


async def probe_ka10059(token):
	"""외인/기관/개인 일별 매매 — 필수: dt, amt_qty_tp, trde_tp."""
	print('\n' + '=' * 60)
	print('ka10059 외인기관 일별 매매 (재시도: trde_tp 추가)')
	print('=' * 60)
	last_trading_day = '20260519'
	base = {'stk_cd': PROBE_STK, 'dt': last_trading_day, 'amt_qty_tp': '2'}  # 수량 기준
	# trde_tp 후보 — 0:전체/순매수, 1:매도, 2:매수
	for trde_tp in ['0', '1', '2', '3']:
		body = {**base, 'trde_tp': trde_tp, 'unit_tp': '1000'}  # unit_tp=1000:천주
		s, d = await call('ka10059', body, token, '/api/dostk/stkinfo')
		rc = d.get('return_code')
		msg = (d.get('return_msg') or '')[:80]
		print(f'\n--- ka10059 [trde_tp={trde_tp}, unit=1000] → rc={rc} msg={msg}')
		if rc == 0:
			dump(f'ka10059 OK [trde_tp={trde_tp}]', s, d, list_preview=5)
			return
		await asyncio.sleep(0.3)
	# unit_tp 누락 시도
	for trde_tp in ['0', '3']:
		body = {**base, 'trde_tp': trde_tp}
		s, d = await call('ka10059', body, token, '/api/dostk/stkinfo')
		rc = d.get('return_code')
		msg = (d.get('return_msg') or '')[:80]
		print(f'\n--- ka10059 [trde_tp={trde_tp}, no unit] → rc={rc} msg={msg}')
		if rc == 0:
			dump(f'ka10059 OK [trde_tp={trde_tp}, no unit]', s, d, list_preview=5)
			return
		await asyncio.sleep(0.3)


async def probe_ka90013(token):
	"""프로그램매매 일별 — mrkcond × amt_qty_tp(1=금액, 2=수량)."""
	print('\n' + '=' * 60)
	print('ka90013 프로그램매매 일별')
	print('=' * 60)
	today = datetime.now().strftime('%Y%m%d')
	bodies = [
		('amt_qty=1 (금액)', {'stk_cd': PROBE_STK, 'date': today, 'amt_qty_tp': '1'}),
		('amt_qty=2 (수량)', {'stk_cd': PROBE_STK, 'date': today, 'amt_qty_tp': '2'}),
		('no_amt_qty',      {'stk_cd': PROBE_STK, 'date': today}),
		('with_dt',         {'stk_cd': PROBE_STK, 'dt': today, 'amt_qty_tp': '1'}),
	]
	for label, body in bodies:
		s, d = await call('ka90013', body, token, '/api/dostk/mrkcond')
		rc = d.get('return_code')
		msg = (d.get('return_msg') or '')[:60]
		print(f'\n--- ka90013 [{label}] → rc={rc} msg={msg}')
		if rc == 0:
			dump(f'ka90013 OK [{label}]', s, d, list_preview=5)
			# 두 amt_qty_tp 모두 확인하고 싶으므로 break 안 함
		await asyncio.sleep(0.3)


async def main():
	token = await _issue_search_token()
	if not token:
		print('TOKEN_FAIL — search 키 확인 필요')
		return

	await probe_ka10046(token)
	await probe_ka10059(token)
	await probe_ka90013(token)


if __name__ == '__main__':
	asyncio.run(main())
