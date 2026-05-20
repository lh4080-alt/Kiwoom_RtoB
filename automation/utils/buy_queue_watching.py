"""
buy_queue_watching 영속화 — 갭 차단 종목 장중 감시.

09:00 매수 시 갭상승(+5%)/갭하락(-3%) 차단된 종목을 폐기하지 않고 watching 큐로 이동.
watching_buyer가 5분 간격 polling으로 정상 범위 30분 유지 시 매수.

스키마:
{
  "code": "035420",
  "approved_at": "2026-05-19T18:30:00",         # pick 시각 (buy_queue 보존)
  "blocked_at": "2026-05-20T09:00:05",          # 차단 시각
  "block_reason": "blocked_gap_up" | "blocked_gap_down",
  "block_ratio": 1.0696,                        # 시초가/전일종가 비율
  "prev_close": 115000,                         # 전일 종가 (정상 진입 판정 기준)
  "normal_since": null | "ISO",                 # 정상 범위 첫 진입 시각
  "last_check_at": null | "ISO",                # 마지막 polling 시각
  "last_failed_rc": null | int,                 # 마지막 매수 실패 응답코드
  "consecutive_failed_count": 0                 # 같은 rc 연속 실패 횟수
}

영구 원칙 (메모리 #30): 봇 데몬 내부에서만 조작.
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WATCHING_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'buy_queue_watching.json')

_lock = asyncio.Lock()


def _load_sync() -> list:
	"""sync 로드. 내부용, lock 외부."""
	if not os.path.exists(_WATCHING_PATH):
		return []
	try:
		with open(_WATCHING_PATH, 'r', encoding='utf-8') as f:
			data = json.load(f)
		if not isinstance(data, list):
			logger.warning("buy_queue_watching 파일 형식 오류 — 빈 리스트로 초기화")
			return []
		return data
	except json.JSONDecodeError:
		logger.exception("buy_queue_watching 파싱 실패 — 빈 리스트로 초기화")
		return []


def _save_sync(queue: list) -> None:
	os.makedirs(os.path.dirname(_WATCHING_PATH), exist_ok=True)
	tmp = _WATCHING_PATH + '.tmp'
	with open(tmp, 'w', encoding='utf-8') as f:
		json.dump(queue, f, ensure_ascii=False, indent=2)
	os.replace(tmp, _WATCHING_PATH)


async def load_watching() -> list:
	"""watching 큐 로드. 파일 없으면 빈 리스트."""
	async with _lock:
		return _load_sync()


async def save_watching(queue: list) -> None:
	"""watching 큐 저장. atomic."""
	async with _lock:
		_save_sync(queue)


async def add_to_watching(entry: dict) -> bool:
	"""종목 추가. 중복(같은 code)이면 False.

	entry는 최소 code/blocked_at/block_reason/block_ratio/prev_close 필요.
	나머지 필드는 기본값(None/0) 자동 보강.
	"""
	code = entry.get('code')
	if not code:
		return False
	async with _lock:
		queue = _load_sync()
		if any(it.get('code') == code for it in queue):
			return False
		queue.append({
			'code': code,
			'approved_at': entry.get('approved_at'),
			'blocked_at': entry.get('blocked_at', datetime.now().isoformat(timespec='seconds')),
			'block_reason': entry.get('block_reason'),
			'block_ratio': entry.get('block_ratio'),
			'prev_close': entry.get('prev_close'),
			'normal_since': None,
			'last_check_at': None,
			'last_failed_rc': None,
			'consecutive_failed_count': 0,
		})
		_save_sync(queue)
		return True


async def remove_from_watching(code: str) -> bool:
	"""종목 제거. 없으면 False."""
	async with _lock:
		queue = _load_sync()
		before = len(queue)
		queue = [it for it in queue if it.get('code') != code]
		if len(queue) == before:
			return False
		_save_sync(queue)
		return True


async def update_watching(code: str, **fields) -> bool:
	"""entry의 필드 갱신. 존재하지 않으면 False."""
	if not fields:
		return False
	async with _lock:
		queue = _load_sync()
		updated = False
		for it in queue:
			if it.get('code') == code:
				for k, v in fields.items():
					it[k] = v
				updated = True
				break
		if updated:
			_save_sync(queue)
		return updated


async def clear_watching() -> int:
	"""전체 비우기. 비워진 종목 수 반환."""
	async with _lock:
		queue = _load_sync()
		count = len(queue)
		_save_sync([])
		logger.info(f"buy_queue_watching cleared: {count} entries")
		return count
