"""
조건검색 수집종목풀 관리.

조건식에 매칭된 종목을 실시간 매수하지 않고, 풀에 누적 저장한다.
이후 별도 필터링 단계에서 풀을 읽어 매수 대상을 결정한다.

파일: config/data/collection_pool.json
"""
import asyncio
import json
import os
from datetime import datetime

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_POOL_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'collection_pool.json')

_lock = asyncio.Lock()

# 종목명 캐시 (프로세스 수명 동안 유지). 종목명은 거의 변경되지 않아 만료 불필요.
_name_cache: dict = {}
_name_cache_lock = asyncio.Lock()


async def get_stock_name(stk_cd: str) -> str:
	"""종목명 조회 + 캐시. 캐시 미스 시 ka10001 호출, 실패 시 빈 문자열."""
	if not stk_cd:
		return ''
	if stk_cd in _name_cache:
		return _name_cache[stk_cd]
	async with _name_cache_lock:
		if stk_cd in _name_cache:
			return _name_cache[stk_cd]
		try:
			from api.stock_info import get_stock_info
			info = await get_stock_info(stk_cd)
			if isinstance(info, dict):
				name = (info.get('stk_nm') or '').strip()
				if name:
					_name_cache[stk_cd] = name
					return name
		except Exception as e:
			print(f"[수집풀] 종목명 조회 실패 {stk_cd}: {type(e).__name__}: {e}")
	return ''


def _load() -> dict:
	if not os.path.exists(_POOL_PATH):
		return {}
	try:
		with open(_POOL_PATH, 'r', encoding='utf-8') as f:
			data = json.load(f)
		return data if isinstance(data, dict) else {}
	except Exception as e:
		print(f"[수집풀] 로드 실패: {type(e).__name__}: {e} — 빈 풀로 시작합니다.")
		return {}


def _save(data: dict) -> None:
	os.makedirs(os.path.dirname(_POOL_PATH), exist_ok=True)
	tmp = _POOL_PATH + '.tmp'
	with open(tmp, 'w', encoding='utf-8') as f:
		json.dump(data, f, ensure_ascii=False, indent=2)
	os.replace(tmp, _POOL_PATH)


async def add_to_pool(stk_cd, condition_name=None, seq_id=None):
	"""
	조건검색에서 매칭된 종목 1건을 수집풀에 추가/갱신.

	신규 종목이면 새 엔트리를 만들고, 기존 종목이면 last_seen, hit_count,
	conditions, seq_ids를 갱신한다. 매수 동작은 일절 하지 않는다.
	"""
	if not stk_cd:
		return

	now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	seq_str = str(seq_id).strip() if seq_id is not None else None
	cond = condition_name.strip() if isinstance(condition_name, str) and condition_name.strip() else None

	async with _lock:
		pool = _load()
		entry = pool.get(stk_cd)
		if entry is None:
			entry = {
				'stk_cd': stk_cd,
				'first_seen': now,
				'last_seen': now,
				'hit_count': 1,
				'conditions': [cond] if cond else [],
				'seq_ids': [seq_str] if seq_str else [],
			}
		else:
			entry['last_seen'] = now
			entry['hit_count'] = int(entry.get('hit_count', 0)) + 1
			if cond and cond not in entry.get('conditions', []):
				entry.setdefault('conditions', []).append(cond)
			if seq_str and seq_str not in entry.get('seq_ids', []):
				entry.setdefault('seq_ids', []).append(seq_str)
		pool[stk_cd] = entry
		_save(pool)

	tag = f"[{cond}]" if cond else (f"[seq:{seq_str}]" if seq_str else "")
	name = await get_stock_name(stk_cd)
	name_part = f" {name}" if name else ""
	print(f"📥 [수집풀] {stk_cd}{name_part} {tag} — 누적 {entry['hit_count']}회 (총 {len(pool)}종목)")


def get_pool() -> dict:
	"""현재 수집풀 전체를 반환 (필터링/조회용)."""
	return _load()


def clear_pool() -> int:
	"""
	수집풀을 빈 dict로 초기화. 비워진 종목 수 반환.
	동기 호출 가정 (장 마감 후 daily_quality_logger에서만 호출됨).
	"""
	pool = _load()
	count = len(pool)
	_save({})
	return count
