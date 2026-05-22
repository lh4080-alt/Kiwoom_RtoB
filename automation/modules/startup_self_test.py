"""봇 시작 시 1회 실행 — 핵심 API 응답 필드 무결성 점검.

5/22 GST/HL만도 failed_no_price 사고는 ka10001 응답에서 봇이 사용하는 'base_pric' 필드가
없는 줄 모르고 매수 실행 → 두 번 매수 기회 상실. 진단 1주 늦었음.

이 모듈이 봇 시작 시점에 ka10001을 한 번 호출해서 필수 필드 (cur_prc, base_pric, stk_nm)가
모두 살아있는지 확인. 누락 시 텔레그램 알림 + 로그 경고. 키움 API 응답 형식 변경 시
첫 사고 발생 전에 감지 목표.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ka10001 응답에 봇이 매수 가격 결정에 사용하는 필드들
REQUIRED_KA10001_FIELDS = ['cur_prc', 'base_pric', 'stk_nm']

# 점검용 종목 — 삼성전자 (거래 활발, 데이터 안정)
PROBE_STOCK = '005930'


def check_ka10001_fields(resp: dict) -> list:
	"""ka10001 응답 dict에서 필수 필드 누락 여부 검사.

	Returns:
		누락된 필드 리스트 (빈 리스트면 OK)
	"""
	missing = []
	for field in REQUIRED_KA10001_FIELDS:
		value = resp.get(field)
		if value is None or value == '':
			missing.append(field)
	return missing


async def run_startup_self_test(token_manager) -> bool:
	"""봇 시작 시 1회 호출. 비동기 task로 실행 권장 (봇 가동 블로킹 X).

	Returns:
		True: 모든 점검 통과
		False: 토큰 실패 / 응답 오류 / 필드 누락
	"""
	from telegram.tel_send import tel_send
	import utils.config as config
	from utils.rate_limiter import requests

	try:
		token = await token_manager.get_token()
		if not token:
			await tel_send("⚠️ [startup self-test] 토큰 발급 실패 — 봇 동작 비정상 가능")
			logger.warning("startup self-test: token issue failed")
			return False

		# ka10001 raw 호출
		url = config.get_host_url() + '/api/dostk/stkinfo'
		headers = {
			'Content-Type': 'application/json;charset=UTF-8',
			'authorization': f'Bearer {token}',
			'cont-yn': 'N',
			'next-key': '',
			'api-id': 'ka10001',
		}
		r = await requests.post(url, headers=headers, json={'stk_cd': PROBE_STOCK})
		resp = r.json()

		if resp.get('return_code') != 0:
			msg = f"⚠️ [startup self-test] ka10001 응답 오류 rc={resp.get('return_code')}: {resp.get('return_msg')}"
			await tel_send(msg)
			logger.warning(msg)
			return False

		missing = check_ka10001_fields(resp)
		if missing:
			msg = (
				f"⚠️ [startup self-test] ka10001 필드 누락: {', '.join(missing)}\n"
				f"키움 API 응답 형식 변경 가능성. 매수/매도 사고 위험 — 즉시 확인 필요."
			)
			await tel_send(msg)
			logger.warning(f"startup self-test failed: missing fields {missing}")
			return False

		logger.info(
			f"startup self-test OK — ka10001 필드 정상 "
			f"(stk={PROBE_STOCK}, cur_prc={resp.get('cur_prc')}, base_pric={resp.get('base_pric')})"
		)
		return True

	except Exception as e:
		try:
			await tel_send(f"⚠️ [startup self-test] 예외: {type(e).__name__}: {e}")
		except Exception:
			pass
		logger.exception("startup self-test exception")
		return False
