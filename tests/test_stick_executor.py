"""stick_executor 순수 헬퍼 단위 테스트."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestEvaluatePreMarket:
	"""3종목 (SOX/NVDA/MU) 다수결 — 기본 임계값 0.3%, 최소 상승 2/3."""

	def test_all_three_pass(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.4, 0.6)
		assert r['rising_count'] == 3
		assert r['pass'] is True

	def test_two_of_three_pass(self):
		"""SOX/NVDA 통과, MU 미달 → 2/3 통과 (pass)."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.4, 0.1)
		assert r['rising_count'] == 2
		assert r['pass'] is True

	def test_one_of_three_fail(self):
		"""SOX만 통과 → 1/3 → 미달."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.1, 0.1)
		assert r['rising_count'] == 1
		assert r['pass'] is False

	def test_all_fail(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(-0.5, -0.3, -0.4)
		assert r['rising_count'] == 0
		assert r['pass'] is False

	def test_exact_threshold(self):
		"""+0.3% 정확히 = 상승 (>= 비교)."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.3, 0.3, 0.3)
		assert r['rising_count'] == 3
		assert r['pass'] is True

	def test_all_none_fetch_fail(self):
		"""모든 종목 fetch 실패 → fetch_ok=False."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(None, None, None)
		assert r['fetch_ok'] is False
		assert r['pass'] is False

	def test_partial_fetch_fail_counts_as_not_rising(self):
		"""SOX/NVDA 통과 + MU fetch 실패 → fetch_ok=True, rising=2 → pass."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.4, None)
		assert r['fetch_ok'] is True
		assert r['mu_ok'] is False
		assert r['rising_count'] == 2
		assert r['pass'] is True

	def test_two_none_one_pass_fails(self):
		"""SOX 1개만 통과 + 나머지 둘 fetch 실패 → fetch_ok=True, rising=1 → 미달."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, None, None)
		assert r['fetch_ok'] is True
		assert r['rising_count'] == 1
		assert r['pass'] is False

	def test_custom_threshold(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.5, 0.5, threshold=1.0)
		assert r['pass'] is False  # 모두 0.5% < 1.0% → rising_count=0

	def test_custom_min_count_3_of_3(self):
		"""min_count=3 — 3개 모두 상승해야 통과."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.4, 0.1, min_count=3)
		assert r['pass'] is False  # 2/3로는 부족


class TestShouldRetryFetch:

	def test_first_attempt_always_true(self):
		from modules.stick_executor import should_retry_fetch
		now = datetime(2026, 5, 30, 8, 30, 0)
		assert should_retry_fetch(0, None, now) is True

	def test_max_retries_exceeded(self):
		from modules.stick_executor import should_retry_fetch
		now = datetime(2026, 5, 30, 8, 30, 0)
		assert should_retry_fetch(3, now - timedelta(seconds=120), now) is False

	def test_within_gap(self):
		"""마지막 시도 후 60초 미만 → False."""
		from modules.stick_executor import should_retry_fetch
		now = datetime(2026, 5, 30, 8, 31, 0)
		last = now - timedelta(seconds=30)
		assert should_retry_fetch(1, last, now) is False

	def test_after_gap(self):
		"""마지막 시도 후 60초 이상 경과 → True."""
		from modules.stick_executor import should_retry_fetch
		now = datetime(2026, 5, 30, 8, 32, 0)
		last = now - timedelta(seconds=61)
		assert should_retry_fetch(1, last, now) is True

	def test_custom_max_retries(self):
		from modules.stick_executor import should_retry_fetch
		now = datetime(2026, 5, 30, 8, 30, 0)
		assert should_retry_fetch(5, None, now, max_retries=10) is True
		assert should_retry_fetch(10, None, now, max_retries=10) is False


class TestFilterStickToday:

	def test_empty_holdings(self):
		from modules.stick_executor import filter_stick_today
		assert filter_stick_today([], '2026-05-30') == []

	def test_stick_filled_today_included(self):
		from modules.stick_executor import filter_stick_today
		h = [{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-30', 'status': 'filled'}]
		assert filter_stick_today(h, '2026-05-30') == h

	def test_pick_excluded(self):
		"""pick 종목은 동시호가 매도 대상 아님."""
		from modules.stick_executor import filter_stick_today
		h = [{'code': '005930', 'source': 'pick', 'buy_date': '2026-05-30', 'status': 'filled'}]
		assert filter_stick_today(h, '2026-05-30') == []

	def test_yesterday_stick_excluded(self):
		"""어제 산 stick 잔여 — Phase 6에서 알림, 동시호가 매도 X."""
		from modules.stick_executor import filter_stick_today
		h = [{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-29', 'status': 'filled'}]
		assert filter_stick_today(h, '2026-05-30') == []

	def test_pending_fill_excluded(self):
		"""미체결 상태는 09:30 buy_executor가 자동 취소 — 동시호가 매도 X."""
		from modules.stick_executor import filter_stick_today
		h = [{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-30', 'status': 'pending_fill'}]
		assert filter_stick_today(h, '2026-05-30') == []

	def test_no_source_excluded(self):
		"""source 없는 옛 holdings 잔재 — 동시호가 매도 X."""
		from modules.stick_executor import filter_stick_today
		h = [{'code': '005930', 'buy_date': '2026-05-30', 'status': 'filled'}]
		assert filter_stick_today(h, '2026-05-30') == []

	def test_mixed_pick_and_stick(self):
		"""pick + stick 혼재 — stick today filled만 추출."""
		from modules.stick_executor import filter_stick_today
		h = [
			{'code': '005930', 'source': 'pick', 'buy_date': '2026-05-30', 'status': 'filled'},
			{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-30', 'status': 'filled'},
			{'code': '233740', 'source': 'stick', 'buy_date': '2026-05-29', 'status': 'filled'},
			{'code': '396500', 'source': 'stick', 'buy_date': '2026-05-30', 'status': 'pending_fill'},
		]
		result = filter_stick_today(h, '2026-05-30')
		assert len(result) == 1
		assert result[0]['code'] == '122630'


class TestFilterStickLeftover:

	def test_no_leftover(self):
		from modules.stick_executor import filter_stick_leftover
		h = [{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-30', 'status': 'filled'}]
		assert filter_stick_leftover(h, '2026-05-30') == []

	def test_yesterday_stick_leftover(self):
		"""어제 stick 매도 실패 잔여 — 알림 대상."""
		from modules.stick_executor import filter_stick_leftover
		h = [{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-29', 'status': 'filled'}]
		assert len(filter_stick_leftover(h, '2026-05-30')) == 1

	def test_pick_yesterday_excluded(self):
		"""pick 종목은 잔여 알림 대상 아님 (계속 보유)."""
		from modules.stick_executor import filter_stick_leftover
		h = [{'code': '005930', 'source': 'pick', 'buy_date': '2026-05-29', 'status': 'filled'}]
		assert filter_stick_leftover(h, '2026-05-30') == []

	def test_today_stick_excluded(self):
		"""오늘 산 stick은 잔여 아님 (오늘 동시호가 매도 예정)."""
		from modules.stick_executor import filter_stick_leftover
		h = [{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-30', 'status': 'filled'}]
		assert filter_stick_leftover(h, '2026-05-30') == []

	def test_pending_fill_excluded(self):
		from modules.stick_executor import filter_stick_leftover
		h = [{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-29', 'status': 'pending_fill'}]
		assert filter_stick_leftover(h, '2026-05-30') == []

	def test_multi_day_leftover(self):
		"""여러 날 누적된 잔여 — 모두 포함."""
		from modules.stick_executor import filter_stick_leftover
		h = [
			{'code': '122630', 'source': 'stick', 'buy_date': '2026-05-27', 'status': 'filled'},
			{'code': '233740', 'source': 'stick', 'buy_date': '2026-05-29', 'status': 'filled'},
			{'code': '005930', 'source': 'pick', 'buy_date': '2026-05-25', 'status': 'filled'},
		]
		result = filter_stick_leftover(h, '2026-05-30')
		assert len(result) == 2
		assert {r['code'] for r in result} == {'122630', '233740'}
