"""collection_pool.pool_has_today_entry 단위 테스트.

5/22 21건, 5/26 27건, 5/27 풀 손실 사고 회피: 봇 재시작 시 무조건 clear 대신
last_seen 날짜로 분기. 같은 날 데이터면 보존.
"""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestPoolHasTodayEntry:

	def test_empty_pool_returns_false(self):
		from utils.collection_pool import pool_has_today_entry
		assert pool_has_today_entry({}, '2026-05-27') is False
		assert pool_has_today_entry(None, '2026-05-27') is False

	def test_today_entry_returns_true(self):
		"""last_seen이 오늘 — 보존 (clear 안 함)."""
		from utils.collection_pool import pool_has_today_entry
		pool = {
			'005930': {'last_seen': '2026-05-27 09:30:15', 'hit_count': 5},
		}
		assert pool_has_today_entry(pool, '2026-05-27') is True

	def test_yesterday_only_returns_false(self):
		"""어제만 — 잔재로 보고 clear 대상."""
		from utils.collection_pool import pool_has_today_entry
		pool = {
			'005930': {'last_seen': '2026-05-26 15:30:00', 'hit_count': 3},
		}
		assert pool_has_today_entry(pool, '2026-05-27') is False

	def test_mixed_today_and_yesterday(self):
		"""일부 오늘 + 일부 어제 — 오늘 있으면 True (보존)."""
		from utils.collection_pool import pool_has_today_entry
		pool = {
			'005930': {'last_seen': '2026-05-26 15:30:00'},  # 어제
			'011790': {'last_seen': '2026-05-27 09:30:15'},  # 오늘
		}
		assert pool_has_today_entry(pool, '2026-05-27') is True

	def test_missing_last_seen(self):
		"""last_seen 필드 부재 — 안전망 (clear 대상)."""
		from utils.collection_pool import pool_has_today_entry
		pool = {'005930': {'hit_count': 1}}
		assert pool_has_today_entry(pool, '2026-05-27') is False

	def test_none_last_seen(self):
		"""last_seen이 None — 안전망."""
		from utils.collection_pool import pool_has_today_entry
		pool = {'005930': {'last_seen': None}}
		assert pool_has_today_entry(pool, '2026-05-27') is False

	def test_5_26_real_scenario_27_entries_today_preserved(self):
		"""5/26 09:09 재시작 사고 재현 — 09:00 이후 27종목 누적, 같은 날 재시작 시 보존."""
		from utils.collection_pool import pool_has_today_entry
		pool = {
			f'00{i:04d}': {'last_seen': f'2026-05-26 09:{i:02d}:00'}
			for i in range(27)
		}
		assert pool_has_today_entry(pool, '2026-05-26') is True
		# 다음날 재시작이면 어제 잔재로 clear 대상
		assert pool_has_today_entry(pool, '2026-05-27') is False
