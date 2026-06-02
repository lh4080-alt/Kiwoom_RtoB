"""
buy_queue 영속화 — pick/cancel 즉시 flush.

봇 startup 시 load_queue로 조회, daily_analyzer가 16:00에 clear_queue.
영구 원칙: 봇 데몬 내부에서만 조작.
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_QUEUE_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'buy_queue.json')

_lock = asyncio.Lock()


def _load_sync() -> list:
	"""sync 로드. 내부용. lock 외부."""
	if not os.path.exists(_QUEUE_PATH):
		return []
	try:
		with open(_QUEUE_PATH, 'r', encoding='utf-8') as f:
			data = json.load(f)
		if not isinstance(data, list):
			logger.warning("buy_queue 파일 형식 오류 — 빈 리스트로 초기화")
			return []
		return data
	except json.JSONDecodeError:
		logger.exception("buy_queue 파싱 실패 — 빈 리스트로 초기화")
		return []


def _save_sync(queue: list) -> None:
	os.makedirs(os.path.dirname(_QUEUE_PATH), exist_ok=True)
	tmp = _QUEUE_PATH + '.tmp'
	with open(tmp, 'w', encoding='utf-8') as f:
		json.dump(queue, f, ensure_ascii=False, indent=2)
	os.replace(tmp, _QUEUE_PATH)


async def load_queue() -> list:
	"""buy_queue 로드. 파일 없으면 빈 리스트."""
	async with _lock:
		return _load_sync()


async def save_queue(queue: list) -> None:
	"""buy_queue 저장. atomic (tmp + replace)."""
	async with _lock:
		_save_sync(queue)


async def add_to_queue(code: str, approved_by: str = 'telegram', qty: int = 1,
                       source: str = 'pick', tpr=None, slr=None) -> bool:
	"""종목 추가. (code, source) 키로 중복 체크 — 같은 종목이라도 source 다르면 별도 등록 허용.

	source: 'pick' (09:00 지정가) / 'auction' (08:30 동시호가 시장가) / 'touch' (장 중 반등 매수)
	        / 'stick' (자동) — 같은 종목을 여러 source로 동시 감시 가능.
	tpr/slr: per-holding override (None이면 글로벌 fallback).
	"""
	async with _lock:
		queue = _load_sync()
		if any(item.get('code') == code and item.get('source', 'pick') == source for item in queue):
			return False
		entry = {
			'code': code,
			'qty': int(qty) if qty and int(qty) >= 1 else 1,
			'source': source,
			'approved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
			'approved_by': approved_by,
		}
		if tpr is not None:
			entry['tpr'] = float(tpr)
		if slr is not None:
			entry['slr'] = float(slr)
		queue.append(entry)
		_save_sync(queue)
		return True


async def remove_from_queue(code: str, source: str = None) -> bool:
	"""종목 제거. 없으면 False.

	source=None: 그 종목 모든 source 항목 제거 (cancel 명령 — 사용자 의도가 보통 그것).
	source 명시: 해당 source 항목만 제거 (매수 완료/실패 후 정리 — 다른 source 보존).
	"""
	async with _lock:
		queue = _load_sync()
		before = len(queue)
		if source is None:
			queue = [item for item in queue if item.get('code') != code]
		else:
			queue = [item for item in queue
			         if not (item.get('code') == code and item.get('source', 'pick') == source)]
		if len(queue) == before:
			return False
		_save_sync(queue)
		return True


async def clear_queue(source: str = None) -> int:
	"""큐 비우기. 비워진 항목 수 반환.

	source=None: 전체 비움 (daily_analyzer 16:00 reset 용도).
	source 명시: 해당 source 항목만 비움 (buy_executor 09:00 pick 처리 후 등 —
	             다른 source 보존).
	"""
	async with _lock:
		queue = _load_sync()
		if source is None:
			count = len(queue)
			_save_sync([])
		else:
			remaining = [item for item in queue if item.get('source', 'pick') != source]
			count = len(queue) - len(remaining)
			_save_sync(remaining)
		logger.info(f"buy_queue cleared: {count} entries (source={source or 'all'})")
		return count
