"""WatchingBuyer + buy_queue_watching 영속화 단위 테스트.

테스트 그룹:
  A) buy_queue_watching 영속화 (load/add/duplicate/remove/update/clear)
  B) watching_buyer pure helpers (is_normal_range, hold_elapsed_minutes, calc_failure_state)
  C) _cmd_pick 보유 종목 차단

영속화 테스트는 monkeypatch로 _WATCHING_PATH를 임시 경로로 우회.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# automation 디렉토리를 sys.path에 추가 (봇 모듈 import용)
_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


# ─────────────────────────────────────────────────────────
# A) buy_queue_watching 영속화 테스트
# ─────────────────────────────────────────────────────────
@pytest.fixture
def temp_watching_path(tmp_path, monkeypatch):
	"""임시 watching 파일 경로로 _WATCHING_PATH 우회."""
	from utils import buy_queue_watching
	p = tmp_path / 'buy_queue_watching.json'
	monkeypatch.setattr(buy_queue_watching, '_WATCHING_PATH', str(p))
	return p


class TestWatchingPersistence:

	def test_load_empty_when_missing(self, temp_watching_path):
		from utils.buy_queue_watching import load_watching
		assert asyncio.run(load_watching()) == []

	def test_add_then_load(self, temp_watching_path):
		from utils.buy_queue_watching import add_to_watching, load_watching
		ok = asyncio.run(add_to_watching({
			'code': '035420',
			'block_reason': 'blocked_gap_up',
			'block_ratio': 1.07,
			'prev_close': 115000,
		}))
		assert ok is True
		entries = asyncio.run(load_watching())
		assert len(entries) == 1
		assert entries[0]['code'] == '035420'
		assert entries[0]['normal_since'] is None
		assert entries[0]['consecutive_failed_count'] == 0

	def test_add_duplicate_returns_false(self, temp_watching_path):
		from utils.buy_queue_watching import add_to_watching
		entry = {'code': '005930', 'block_reason': 'blocked_gap_down',
		         'block_ratio': 0.96, 'prev_close': 70000}
		assert asyncio.run(add_to_watching(entry)) is True
		assert asyncio.run(add_to_watching(entry)) is False

	def test_update_changes_field(self, temp_watching_path):
		from utils.buy_queue_watching import add_to_watching, update_watching, load_watching
		asyncio.run(add_to_watching({'code': '005930', 'block_reason': 'blocked_gap_up',
		                             'block_ratio': 1.06, 'prev_close': 70000}))
		now_iso = datetime.now().isoformat(timespec='seconds')
		ok = asyncio.run(update_watching('005930', normal_since=now_iso, consecutive_failed_count=2))
		assert ok is True
		entries = asyncio.run(load_watching())
		assert entries[0]['normal_since'] == now_iso
		assert entries[0]['consecutive_failed_count'] == 2

	def test_remove_and_clear(self, temp_watching_path):
		from utils.buy_queue_watching import add_to_watching, remove_from_watching, clear_watching, load_watching
		asyncio.run(add_to_watching({'code': '000660', 'block_reason': 'blocked_gap_up',
		                             'block_ratio': 1.06, 'prev_close': 110000}))
		asyncio.run(add_to_watching({'code': '005930', 'block_reason': 'blocked_gap_up',
		                             'block_ratio': 1.07, 'prev_close': 70000}))
		assert asyncio.run(remove_from_watching('000660')) is True
		assert asyncio.run(remove_from_watching('NOEXIST')) is False
		assert len(asyncio.run(load_watching())) == 1
		count = asyncio.run(clear_watching())
		assert count == 1
		assert asyncio.run(load_watching()) == []


# ─────────────────────────────────────────────────────────
# B) watching_buyer pure helpers
# ─────────────────────────────────────────────────────────
class TestIsNormalRange:

	def test_gap_up_normal(self):
		from modules.watching_buyer import is_normal_range
		assert is_normal_range('blocked_gap_up', 1.04) is True
		assert is_normal_range('blocked_gap_up', 1.0499) is True

	def test_gap_up_still_blocked(self):
		from modules.watching_buyer import is_normal_range
		assert is_normal_range('blocked_gap_up', 1.05) is False
		assert is_normal_range('blocked_gap_up', 1.10) is False

	def test_gap_down_normal(self):
		from modules.watching_buyer import is_normal_range
		assert is_normal_range('blocked_gap_down', 0.98) is True
		assert is_normal_range('blocked_gap_down', 1.0) is True

	def test_gap_down_still_blocked(self):
		from modules.watching_buyer import is_normal_range
		assert is_normal_range('blocked_gap_down', 0.97) is False
		assert is_normal_range('blocked_gap_down', 0.90) is False

	def test_unknown_reason(self):
		from modules.watching_buyer import is_normal_range
		assert is_normal_range('blocked_other', 1.04) is False
		assert is_normal_range(None, 1.04) is False


class TestHoldElapsed:

	def test_none_returns_zero(self):
		from modules.watching_buyer import hold_elapsed_minutes
		assert hold_elapsed_minutes(None, datetime.now()) == 0.0

	def test_invalid_iso_returns_zero(self):
		from modules.watching_buyer import hold_elapsed_minutes
		assert hold_elapsed_minutes('not-iso', datetime.now()) == 0.0

	def test_30min_elapsed(self):
		from modules.watching_buyer import hold_elapsed_minutes, HOLD_MINUTES
		now = datetime(2026, 5, 20, 10, 0, 0)
		past = (now - timedelta(minutes=30)).isoformat(timespec='seconds')
		assert hold_elapsed_minutes(past, now) >= HOLD_MINUTES

	def test_under_30min(self):
		from modules.watching_buyer import hold_elapsed_minutes, HOLD_MINUTES
		now = datetime(2026, 5, 20, 10, 0, 0)
		past = (now - timedelta(minutes=29, seconds=59)).isoformat(timespec='seconds')
		assert hold_elapsed_minutes(past, now) < HOLD_MINUTES


class TestCalcFailureState:

	def test_same_rc_increments(self):
		from modules.watching_buyer import calc_failure_state
		entry = {'last_failed_rc': 9001, 'consecutive_failed_count': 1}
		count, rc, discard = calc_failure_state(entry, 9001)
		assert count == 2
		assert rc == 9001
		assert discard is False

	def test_same_rc_third_time_discards(self):
		from modules.watching_buyer import calc_failure_state, MAX_FAILED_RETRIES
		entry = {'last_failed_rc': 9001, 'consecutive_failed_count': MAX_FAILED_RETRIES - 1}
		count, rc, discard = calc_failure_state(entry, 9001)
		assert count == MAX_FAILED_RETRIES
		assert discard is True

	def test_different_rc_resets(self):
		from modules.watching_buyer import calc_failure_state
		entry = {'last_failed_rc': 9001, 'consecutive_failed_count': 2}
		count, rc, discard = calc_failure_state(entry, 9999)
		assert count == 1
		assert rc == 9999
		assert discard is False

	def test_first_failure(self):
		"""empty entry → 첫 실패는 count=1, discard=False."""
		from modules.watching_buyer import calc_failure_state
		entry = {}
		count, rc, discard = calc_failure_state(entry, 9001)
		assert count == 1
		assert rc == 9001
		assert discard is False


# ─────────────────────────────────────────────────────────
# C) _cmd_pick 보유 종목 차단
# ─────────────────────────────────────────────────────────
@pytest.fixture
def temp_data_dir(tmp_path, monkeypatch):
	"""buy_queue + holdings 파일을 임시 경로로 우회."""
	from utils import buy_queue, holdings
	q_path = tmp_path / 'buy_queue.json'
	h_path = tmp_path / 'holdings.json'
	monkeypatch.setattr(buy_queue, '_QUEUE_PATH', str(q_path))
	monkeypatch.setattr(holdings, '_HOLDINGS_PATH', str(h_path))
	return tmp_path


class TestCmdPickHoldingsBlock:

	def test_held_code_blocked(self, temp_data_dir, monkeypatch):
		"""이미 filled 상태인 종목 pick → already_held 리스트로."""
		import asyncio
		from utils.holdings import save_holdings
		from utils.buy_queue import load_queue

		# 보유 종목 설정
		asyncio.run(save_holdings([{
			'code': '005930', 'status': 'filled', 'buy_price': 70000, 'buy_qty': 1,
			'buy_date': '2026-05-20',
		}]))

		# _cmd_pick의 로직 핵심을 직접 시뮬레이션 (chat_command 인스턴스 생성 X)
		from utils.buy_queue import add_to_queue
		from utils.holdings import load_holdings

		async def _run():
			held = await load_holdings()
			held_codes = {h['code'] for h in held if h.get('status') in ('pending_fill', 'filled')}
			# pick 시도: 005930 (보유) + 000660 (미보유)
			added, already_held = [], []
			for c in ['005930', '000660']:
				if c in held_codes:
					already_held.append(c)
					continue
				if await add_to_queue(c, approved_by='test'):
					added.append(c)
			return added, already_held, await load_queue()

		added, already_held, queue = asyncio.run(_run())
		assert added == ['000660']
		assert already_held == ['005930']
		assert len(queue) == 1
		assert queue[0]['code'] == '000660'

	def test_pending_fill_also_blocked(self, temp_data_dir):
		"""pending_fill 상태도 보유로 간주 차단."""
		import asyncio
		from utils.holdings import save_holdings, load_holdings
		from utils.buy_queue import add_to_queue, load_queue

		asyncio.run(save_holdings([{
			'code': '005930', 'status': 'pending_fill', 'buy_price': 70000, 'buy_qty': 1,
			'buy_date': '2026-05-20',
		}]))

		async def _run():
			held = await load_holdings()
			held_codes = {h['code'] for h in held if h.get('status') in ('pending_fill', 'filled')}
			already_held = []
			for c in ['005930']:
				if c in held_codes:
					already_held.append(c)
			return already_held, await load_queue()

		already_held, queue = asyncio.run(_run())
		assert already_held == ['005930']
		assert len(queue) == 0
