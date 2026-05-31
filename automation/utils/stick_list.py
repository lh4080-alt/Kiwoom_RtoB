"""
stick_list 영속화 — 매일 반복 매수 대상 종목.

stick은 pick과 다르게 영구 등록 (16:00 daily_analyzer가 clear 안 함).
08:30 SOX/NQ 조건 충족 시 buy_queue에 추가, 09:00 buy_executor가 매수.

entry 구조:
  {
    "code": "122630",
    "qty": 5,
    "tpr": 3,        # 익절 % (option, 없으면 글로벌)
    "slr": 2,        # 손절 % (양수 입력, 내부에서 음수 처리 — option)
    "registered_at": "2026-05-29 14:00:00"
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
_STICK_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'stick_list.json')

_lock = asyncio.Lock()


def _load_sync() -> list:
	if not os.path.exists(_STICK_PATH):
		return []
	try:
		with open(_STICK_PATH, 'r', encoding='utf-8') as f:
			data = json.load(f)
		if not isinstance(data, list):
			logger.warning("stick_list 파일 형식 오류 — 빈 리스트로 초기화")
			return []
		return data
	except json.JSONDecodeError:
		logger.exception("stick_list 파싱 실패 — 빈 리스트로 초기화")
		return []


def _save_sync(items: list) -> None:
	os.makedirs(os.path.dirname(_STICK_PATH), exist_ok=True)
	tmp = _STICK_PATH + '.tmp'
	with open(tmp, 'w', encoding='utf-8') as f:
		json.dump(items, f, ensure_ascii=False, indent=2)
	os.replace(tmp, _STICK_PATH)


async def load_stick() -> list:
	async with _lock:
		return _load_sync()


async def add_stick(code: str, qty: int = 1, tpr: Optional[float] = None,
                    slr: Optional[float] = None) -> bool:
	"""stick 등록. 중복이면 False (덮어쓰려면 cancel 후 다시 add)."""
	async with _lock:
		items = _load_sync()
		if any(it.get('code') == code for it in items):
			return False
		entry = {
			'code': code,
			'qty': int(qty) if qty and int(qty) >= 1 else 1,
			'registered_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
		}
		if tpr is not None:
			entry['tpr'] = float(tpr)
		if slr is not None:
			# 양수 입력 시 음수 변환 (기존 slr_command 패턴 동일)
			s = float(slr)
			entry['slr'] = -abs(s)
		items.append(entry)
		_save_sync(items)
		return True


async def remove_stick(code: str) -> bool:
	async with _lock:
		items = _load_sync()
		before = len(items)
		items = [it for it in items if it.get('code') != code]
		if len(items) == before:
			return False
		_save_sync(items)
		return True


def find_stick(items: list, code: str) -> Optional[dict]:
	"""순수 헬퍼 — 단위 테스트용."""
	for it in items:
		if it.get('code') == code:
			return it
	return None
