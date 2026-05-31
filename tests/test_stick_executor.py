"""stick_executor 순수 헬퍼 단위 테스트."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestEvaluatePreMarket:

	def test_both_pass(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.4)
		assert r == {'fetch_ok': True, 'sox_ok': True, 'nq_ok': True, 'pass': True}

	def test_sox_fail(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.1, 0.5)
		assert r == {'fetch_ok': True, 'sox_ok': False, 'nq_ok': True, 'pass': False}

	def test_nq_fail(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.1)
		assert r == {'fetch_ok': True, 'sox_ok': True, 'nq_ok': False, 'pass': False}

	def test_both_fail(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(-0.5, -0.3)
		assert r['pass'] is False
		assert r['sox_ok'] is False
		assert r['nq_ok'] is False

	def test_exact_threshold(self):
		"""+0.3% 정확히 = 통과 (>= 비교)."""
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.3, 0.3)
		assert r['pass'] is True

	def test_fetch_fail_none(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(None, 0.5)
		assert r == {'fetch_ok': False, 'sox_ok': False, 'nq_ok': False, 'pass': False}

	def test_custom_threshold(self):
		from modules.stick_executor import evaluate_pre_market
		r = evaluate_pre_market(0.5, 0.5, threshold=1.0)
		assert r['pass'] is False  # 0.5% < 1.0%


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
