"""
새 봇이 매수한 보유 종목 영속화 (config/data/holdings.json).

기존 5종목(005380/005930/012330/396500/445290)과 별개로, Phase 2 buy_executor가
매수한 종목만 관리. 매수 시 add_holding, 매도/취소 시 remove_holding.

영구 원칙 (메모리 #30): 봇 데몬 내부에서만 조작.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HOLDINGS_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'holdings.json')

_lock = asyncio.Lock()


def _load_sync() -> list:
	if not os.path.exists(_HOLDINGS_PATH):
		return []
	try:
		with open(_HOLDINGS_PATH, 'r', encoding='utf-8') as f:
			data = json.load(f)
		if not isinstance(data, list):
			logger.warning("holdings 파일 형식 오류 — 빈 리스트")
			return []
		return data
	except json.JSONDecodeError:
		logger.exception("holdings 파싱 실패")
		return []


def _save_sync(holdings: list) -> None:
	os.makedirs(os.path.dirname(_HOLDINGS_PATH), exist_ok=True)
	tmp = _HOLDINGS_PATH + '.tmp'
	with open(tmp, 'w', encoding='utf-8') as f:
		json.dump(holdings, f, ensure_ascii=False, indent=2)
	os.replace(tmp, _HOLDINGS_PATH)


async def load_holdings() -> list:
	"""봇 startup 시 호출. 파일 없으면 빈 리스트."""
	async with _lock:
		return _load_sync()


async def save_holdings(holdings: list) -> None:
	"""holdings 저장 (atomic)."""
	async with _lock:
		_save_sync(holdings)


async def add_holding(holding: dict) -> None:
	"""매수 주문 후 즉시 호출.

	holding 권장 키:
	  code, buy_price, buy_qty, buy_date, buy_datetime, ord_no,
	  stop_loss_price, sell_deadline, status (pending_fill / filled)
	"""
	async with _lock:
		holdings = _load_sync()
		holdings.append(holding)
		_save_sync(holdings)
		logger.info(f"[holdings] 추가: {holding.get('code')} @ {holding.get('buy_price')}")


async def update_holding(code: str, patch: dict) -> bool:
	"""기존 holding의 일부 필드만 갱신. 없으면 False."""
	async with _lock:
		holdings = _load_sync()
		found = False
		for h in holdings:
			if h.get('code') == code:
				h.update(patch)
				found = True
				break
		if found:
			_save_sync(holdings)
			logger.info(f"[holdings] 갱신: {code} <- {patch}")
		return found


async def remove_holding(code: str) -> Optional[dict]:
	"""매도/취소 완료 시 호출. 제거된 항목 반환 (없으면 None)."""
	async with _lock:
		holdings = _load_sync()
		target = next((h for h in holdings if h.get('code') == code), None)
		if target is None:
			return None
		holdings = [h for h in holdings if h.get('code') != code]
		_save_sync(holdings)
		logger.info(f"[holdings] 제거: {code}")
		return target


_override_cache: dict = {'data': None, 'time': 0.0}


def get_holding_override(code: str, cache_sec: int = 5) -> dict:
	"""종목별 tpr/slr override 조회 (sync, 캐시 5초).

	Feature 2 (websocket.py _handle_stock_quote)에서 매 0B push마다 호출되므로
	파일 I/O를 캐시로 최소화. 같은 종목 여러 entry면 첫 entry 사용 (stick 등록은
	한 종목당 한 번이므로 보통 유일).

	Returns: {'tpr': float|None, 'slr': float|None}
	"""
	import time as _time
	now = _time.time()
	if _override_cache['data'] is None or (now - _override_cache['time']) > cache_sec:
		_override_cache['data'] = _load_sync()
		_override_cache['time'] = now
	for h in _override_cache['data']:
		if h.get('code') == code:
			return {'tpr': h.get('tpr'), 'slr': h.get('slr')}
	return {'tpr': None, 'slr': None}


def calc_sell_deadline(buy_date: str, days: int = 5) -> str:
	"""buy_date + N 영업일 (월~금 카운트, 공휴일 미고려)."""
	dt = datetime.strptime(buy_date, '%Y-%m-%d')
	added = 0
	while added < days:
		dt += timedelta(days=1)
		if dt.weekday() < 5:  # 월~금
			added += 1
	return dt.strftime('%Y-%m-%d')
