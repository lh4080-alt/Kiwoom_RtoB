"""semi_trigger 전용 키움 토큰 발급 — search 키(-XMf61) 사용.

왜 분리하나 (2026-07-21): RtoB 기본 발급 경로(utils.config.get_app_key =
real_app_key.txt)는 Kiwoom_Basic 매매봇과 같은 키(GDLLsq)라, semi가 토큰을
발급하면 키움의 재발급 무효화 정책에 따라 Basic의 REST 토큰이 죽을 수 있다
(7/20 KB 일일분석 전멸 사고 조사에서 도출). semi는 config/search_app_key.txt
(-XMf61)로 분리 발급한다.

-XMf61은 MDC·DCAbot·3protv와 공유하는 조회 풀이며, 풀 관례상 다른 봇의
재발급으로 내 토큰이 무효(8005)가 되면 force 재발급 후 1회 재시도한다 —
조회 전용이라 재시도에 부작용이 없다. (재시도는 pipeline 쪽에서 수행)
"""
import logging

logger = logging.getLogger(__name__)

# search(-XMf61) 키는 실전 host 사용 (api_probe.py와 동일)
_HOST_URL = 'https://api.kiwoom.com'

_token = None


async def get_semi_token(force: bool = False):
	"""캐시된 semi 전용 토큰 반환. force=True면 재발급."""
	global _token
	if _token and not force:
		return _token
	from utils.config import read_config_file
	from utils.rate_limiter import requests

	app_key = read_config_file('search_app_key.txt')
	app_secret = read_config_file('search_app_secret.txt')
	if not app_key or not app_secret:
		logger.error('[semi_token] config/search_app_key.txt/secret 없음 — 발급 불가')
		return None
	try:
		resp = await requests.post(
			_HOST_URL + '/oauth2/token',
			headers={'Content-Type': 'application/json;charset=UTF-8'},
			json={
				'grant_type': 'client_credentials',
				'appkey': app_key,
				'secretkey': app_secret,
			},
		)
		data = resp.json()
	except Exception as e:
		logger.error(f'[semi_token] 발급 요청 실패: {type(e).__name__}: {e}')
		return None
	token = data.get('token')
	if not token:
		logger.error(f'[semi_token] 발급 거부: {str(data)[:200]}')
		return None
	_token = token
	logger.info(f'[semi_token] 발급 완료 ({token[:8]}..., expires={data.get("expires_dt")})')
	return _token
