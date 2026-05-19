"""
키움 API 클라이언트 — 계정별 인스턴스 분리.

Phase 1 (현재): 클래스 정의만. 봇 startup 통합은 Phase 2 (계정 분리 시).
각 인스턴스가 독립 토큰 + WebSocket 관리.

시크릿 파일 매핑:
  search 계정 → config/search_app_key.txt, search_app_secret.txt, search_account_no.txt
  trade 계정  → config/trade_app_key.txt, trade_app_secret.txt, trade_account_no.txt

현재 매핑 (Lee 결정 2026-05-19):
  - 기존 real_app_key.txt = search 계정 (조건검색 수집)
  - trade 계정 신규 발급 예정
"""
import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_BASE_DIR, 'config')


def _read_secret(filename: str) -> str:
	"""config/<filename> 텍스트 파일에서 값 읽기. 없으면 빈 문자열."""
	path = os.path.join(_CONFIG_DIR, filename)
	try:
		with open(path, 'r', encoding='utf-8') as f:
			return f.read().strip()
	except FileNotFoundError:
		return ''


class KiwoomClient:
	"""
	계정별 키움 API 클라이언트.

	각 인스턴스가 독립 토큰 + WebSocket 관리.
	기존 utils/config.py, api/login.py 등의 전역 함수와 병행 가능 — Phase 1은 클래스 정의만,
	봇 startup 통합은 Phase 2부터.
	"""

	def __init__(
		self,
		name: str,
		app_key: str,
		app_secret: str,
		account_no: str,
		host_url: str = 'https://api.kiwoom.com',
		socket_url: str = 'wss://api.kiwoom.com:10000',
	):
		"""
		Args:
			name: 'search' 또는 'trade' (로그 식별용)
			app_key, app_secret, account_no: 계정별 시크릿
			host_url: REST API base URL (실계좌 default)
			socket_url: WebSocket base URL (실계좌 default)
		"""
		self.name = name
		self.app_key = app_key
		self.app_secret = app_secret
		self.account_no = account_no
		self.host_url = host_url
		self.socket_url = socket_url

		self.token: Optional[str] = None
		self.token_expires_at: Optional[str] = None  # 'YYYYMMDDHHMMSS'
		self.ws = None
		self.ws_connected = False

		self.logger = logging.getLogger(f"{__name__}.{name}")

	async def authenticate(self) -> str:
		"""OAuth 토큰 발급 (au10001). 성공 시 self.token 설정 + 반환."""
		from utils.rate_limiter import requests

		endpoint = '/oauth2/token'
		url = self.host_url + endpoint
		headers = {'Content-Type': 'application/json;charset=UTF-8'}
		data = {
			'grant_type': 'client_credentials',
			'appkey': self.app_key,
			'secretkey': self.app_secret,
		}
		response = await requests.post(url, headers=headers, json=data)
		body = response.json()
		token = body.get('token')
		expires = body.get('expires_dt')
		if not token:
			self.logger.error(f"[{self.name}] 토큰 발급 실패: {body.get('return_msg')}")
			return ''
		self.token = token
		self.token_expires_at = expires
		self.logger.info(f"[{self.name}] 로그인 성공 (만료 {expires})")
		return token

	async def call_api(self, api_id: str, body: dict, cont_yn: str = 'N', next_key: str = '') -> dict:
		"""
		REST API 호출. 토큰 자동 첨부.

		Args:
			api_id: TR ID (예: 'ka10001', 'kt10000')
			body: 요청 본문 dict
			cont_yn / next_key: 연속조회 옵션
		"""
		from utils.rate_limiter import requests

		if not self.token:
			await self.authenticate()
		if not self.token:
			raise RuntimeError(f"[{self.name}] 토큰 없음 — API 호출 불가")

		# api_id로 endpoint 추론은 호출자가 명시적으로 지정하는 게 안전
		# Phase 1에서는 generic endpoint 사용 안 함. 기존 api/*.py 함수를 통해 호출 유지.
		raise NotImplementedError(
			"call_api는 Phase 2에서 endpoint 매핑과 함께 구현. "
			"Phase 1에서는 기존 api/*.py 함수 사용."
		)

	def __repr__(self):
		return f"<KiwoomClient name={self.name} authed={bool(self.token)}>"


def load_search_client() -> KiwoomClient:
	"""
	계정1 (search) 클라이언트 로드.

	우선순위: search_*.txt 파일 → 없으면 real_*.txt fallback (현재 봇 호환).
	"""
	app_key = _read_secret('search_app_key.txt') or _read_secret('real_app_key.txt')
	app_secret = _read_secret('search_app_secret.txt') or _read_secret('real_app_secret.txt')
	account_no = _read_secret('search_account_no.txt')
	if not app_key or not app_secret:
		raise FileNotFoundError(
			"search 계정 시크릿 없음. config/search_app_key.txt + search_app_secret.txt "
			"(또는 fallback real_*.txt) 필요."
		)
	return KiwoomClient(
		name='search',
		app_key=app_key,
		app_secret=app_secret,
		account_no=account_no,
	)


def load_trade_client() -> KiwoomClient:
	"""
	계정2 (trade) 클라이언트 로드. 신규 발급 시크릿 필요.
	"""
	app_key = _read_secret('trade_app_key.txt')
	app_secret = _read_secret('trade_app_secret.txt')
	account_no = _read_secret('trade_account_no.txt')
	if not app_key or not app_secret:
		raise FileNotFoundError(
			"trade 계정 시크릿 없음. config/trade_app_key.txt + trade_app_secret.txt 신규 발급 필요."
		)
	return KiwoomClient(
		name='trade',
		app_key=app_key,
		app_secret=app_secret,
		account_no=account_no,
	)
