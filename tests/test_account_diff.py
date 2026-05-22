"""09:35 잔고-봇 정합성 검증 헬퍼 단위 테스트.

진단 시나리오:
  - 봇이 매수 안 했다고 생각 / 실 계좌엔 있음 → kt10000 ord_no 추출 실패 가능성
  - 봇이 매수했다고 기록 / 실 계좌엔 없음 → 취소 누락 또는 체결 실패 미감지
  - 옛 5종목 (LEGACY_HELD_CODES)은 noise 회피 위해 비교 제외
"""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestDiffAccountVsHoldings:

	def test_perfect_match(self):
		from modules.buy_executor import diff_account_vs_holdings
		acc = {'003490', '052400'}
		bot = {'003490', '052400'}
		only_acc, only_bot = diff_account_vs_holdings(acc, bot)
		assert only_acc == set()
		assert only_bot == set()

	def test_account_only_non_legacy(self):
		"""봇 모르게 계좌에 있는 종목 — kt10000 응답 파싱 실패 의심."""
		from modules.buy_executor import diff_account_vs_holdings
		acc = {'003490', '052400'}
		bot = {'003490'}
		only_acc, only_bot = diff_account_vs_holdings(acc, bot)
		assert only_acc == {'052400'}
		assert only_bot == set()

	def test_bot_only(self):
		"""봇 holdings에 있지만 계좌엔 없음 — 취소 누락 / 체결 실패 미감지."""
		from modules.buy_executor import diff_account_vs_holdings
		acc = {'003490'}
		bot = {'003490', '052400'}
		only_acc, only_bot = diff_account_vs_holdings(acc, bot)
		assert only_acc == set()
		assert only_bot == {'052400'}

	def test_legacy_codes_excluded_from_account_only(self):
		"""옛 5종목이 계좌에 있어도 only_in_account에 안 잡힘 (noise 회피)."""
		from modules.buy_executor import diff_account_vs_holdings, LEGACY_HELD_CODES
		acc = {'005380', '005930', '012330', '396500', '445290', '003490'}
		bot = {'003490'}
		only_acc, only_bot = diff_account_vs_holdings(acc, bot)
		assert only_acc == set()  # 옛 5종목 다 제외, 003490은 봇에도 있음
		assert only_bot == set()
		# LEGACY_HELD_CODES 자체는 핸드오프와 일치
		assert LEGACY_HELD_CODES == {'005380', '005930', '012330', '396500', '445290'}

	def test_legacy_in_account_plus_unknown(self):
		"""옛 5종목 + 봇 모르는 신규 = 신규만 잡힘."""
		from modules.buy_executor import diff_account_vs_holdings
		acc = {'005380', '005930', '052400'}
		bot = set()
		only_acc, only_bot = diff_account_vs_holdings(acc, bot)
		assert only_acc == {'052400'}
		assert only_bot == set()

	def test_legacy_purchased_again_today(self):
		"""봇이 오늘 005930 새로 매수해서 holdings에도 있는 케이스 —
		legacy 제외 로직이 봇에 있는 종목까지 제외하지 않음."""
		from modules.buy_executor import diff_account_vs_holdings
		acc = {'005930'}
		bot = {'005930'}
		only_acc, only_bot = diff_account_vs_holdings(acc, bot)
		# 둘 다 있으면 차집합 자체에 안 들어감 → legacy 필터링 무관
		assert only_acc == set()
		assert only_bot == set()
