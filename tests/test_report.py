"""Phase 6c — IS/OOS 리포트 헬퍼 단위 테스트."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestSplitWindow:

	def test_70_30_split(self):
		from modules.semi_trigger.report import split_window
		dates = list(range(100))
		is_, oos = split_window(dates, ratio=0.7)
		assert len(is_) == 70
		assert len(oos) == 30
		assert is_[-1] == 69
		assert oos[0] == 70

	def test_empty(self):
		from modules.semi_trigger.report import split_window
		is_, oos = split_window([])
		assert is_ == []
		assert oos == []

	def test_single_item(self):
		from modules.semi_trigger.report import split_window
		is_, oos = split_window(['2026-06-01'])
		# 1 × 0.7 = 0 (int) → is=[], oos=[item]
		assert is_ == []
		assert oos == ['2026-06-01']


class TestPickBestThreshold:

	def test_pick_by_sharpe(self):
		from modules.semi_trigger.report import pick_best_threshold
		grid = [
			{'threshold': 0.5, 'semi': {'n': 30, 'sharpe': 0.2, 'mean': 1.0}},
			{'threshold': 1.0, 'semi': {'n': 20, 'sharpe': 0.5, 'mean': 1.5}},  # best
			{'threshold': 1.5, 'semi': {'n': 10, 'sharpe': 0.3, 'mean': 2.0}},
		]
		best = pick_best_threshold(grid, criterion='sharpe')
		assert best['threshold'] == 1.0

	def test_pick_by_mean(self):
		from modules.semi_trigger.report import pick_best_threshold
		grid = [
			{'threshold': 0.5, 'semi': {'n': 30, 'sharpe': 0.2, 'mean': 1.0}},
			{'threshold': 1.5, 'semi': {'n': 10, 'sharpe': 0.3, 'mean': 2.0}},  # best
		]
		best = pick_best_threshold(grid, criterion='mean')
		assert best['threshold'] == 1.5

	def test_min_n_filter(self):
		"""min_n=10 → n<10 임계값 제외."""
		from modules.semi_trigger.report import pick_best_threshold
		grid = [
			{'threshold': 1.0, 'semi': {'n': 20, 'sharpe': 0.5, 'mean': 1.0}},
			{'threshold': 2.0, 'semi': {'n': 3, 'sharpe': 1.0, 'mean': 5.0}},  # 제외
		]
		best = pick_best_threshold(grid, criterion='sharpe', min_n=10)
		assert best['threshold'] == 1.0

	def test_all_filtered_returns_none(self):
		from modules.semi_trigger.report import pick_best_threshold
		grid = [
			{'threshold': 1.0, 'semi': {'n': 2, 'sharpe': 0.5}},
		]
		assert pick_best_threshold(grid, min_n=5) is None

	def test_none_sharpe_excluded(self):
		"""sharpe=None은 사실상 -inf — 다른 row가 선택됨."""
		from modules.semi_trigger.report import pick_best_threshold
		grid = [
			{'threshold': 0.5, 'semi': {'n': 30, 'sharpe': None, 'mean': 10.0}},
			{'threshold': 1.0, 'semi': {'n': 20, 'sharpe': 0.1, 'mean': 1.0}},
		]
		best = pick_best_threshold(grid, criterion='sharpe')
		assert best['threshold'] == 1.0


class TestDecideVerdict:

	def test_pass_semi_better(self):
		"""semi mean > legacy + win 비슷 + MDD 비슷 → pass_semi_better."""
		from modules.semi_trigger.report import _decide_verdict
		oos_semi = {'n': 10, 'mean': 3.0, 'win_rate': 0.7, 'max_dd': 5.0}
		oos_legacy = {'n': 20, 'mean': 1.5, 'win_rate': 0.6, 'max_dd': 6.0}
		assert _decide_verdict(oos_semi, oos_legacy) == 'pass_semi_better'

	def test_fail_semi_worse(self):
		from modules.semi_trigger.report import _decide_verdict
		oos_semi = {'n': 10, 'mean': 0.5, 'win_rate': 0.4, 'max_dd': 8.0}
		oos_legacy = {'n': 20, 'mean': 1.5, 'win_rate': 0.6, 'max_dd': 5.0}
		assert _decide_verdict(oos_semi, oos_legacy) == 'fail_semi_worse'

	def test_insufficient_data(self):
		from modules.semi_trigger.report import _decide_verdict
		oos_semi = {'n': 2, 'mean': 5.0, 'win_rate': 1.0, 'max_dd': 0}
		oos_legacy = {'n': 20, 'mean': 1.5, 'win_rate': 0.6, 'max_dd': 5.0}
		assert _decide_verdict(oos_semi, oos_legacy) == 'insufficient_data'

	def test_pass_close_mean_better_win_lower(self):
		"""mean 우위지만 win/MDD 살짝 떨어짐 → pass_close."""
		from modules.semi_trigger.report import _decide_verdict
		oos_semi = {'n': 10, 'mean': 2.5, 'win_rate': 0.5, 'max_dd': 10.0}
		oos_legacy = {'n': 20, 'mean': 2.0, 'win_rate': 0.6, 'max_dd': 6.0}
		# mean > / win < 0.95×legacy / mdd > 1.2×legacy
		# mean_ok=True, win_ok=False(0.5 < 0.6×0.95=0.57), mdd_ok=False(10>6×1.2=7.2)
		# → mean_ok AND (win_ok or mdd_ok) = False → fail
		assert _decide_verdict(oos_semi, oos_legacy) == 'fail_semi_worse'

	def test_mean_better_win_close(self):
		from modules.semi_trigger.report import _decide_verdict
		oos_semi = {'n': 10, 'mean': 2.5, 'win_rate': 0.58, 'max_dd': 10.0}
		oos_legacy = {'n': 20, 'mean': 2.0, 'win_rate': 0.6, 'max_dd': 6.0}
		# win_ok=True(0.58 >= 0.57), mdd_ok=False → pass_close
		assert _decide_verdict(oos_semi, oos_legacy) == 'pass_close'
