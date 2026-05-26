"""check_bid.parse_sel_fpr_bid 단위 테스트.

5/26 ka10004 raw probe로 확정: 응답에 'sel_fpr_bid' (매도 최우선 호가).
키움이 가격대 표시 부호 사용 — 상승 시 '+299500', 하락 시 '-298500'.
abs() 절댓값 처리로 둘 다 양수 호가로 반환해야 매수 가격으로 사용 가능.
"""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestParseSelFprBid:

	def test_positive_with_plus_sign(self):
		"""상승장 — '+299500' → 299500.0 (probe 검증된 형태)."""
		from api.check_bid import parse_sel_fpr_bid
		resp = {'sel_fpr_bid': '+299500'}
		assert parse_sel_fpr_bid(resp) == 299500.0

	def test_negative_sign_absolute_value(self):
		"""하락장 — '-298500' → 298500.0 (abs 처리, Lee 요청 검증)."""
		from api.check_bid import parse_sel_fpr_bid
		resp = {'sel_fpr_bid': '-298500'}
		assert parse_sel_fpr_bid(resp) == 298500.0

	def test_no_sign_plain_number(self):
		"""부호 없는 응답 — '299500' → 299500.0."""
		from api.check_bid import parse_sel_fpr_bid
		resp = {'sel_fpr_bid': '299500'}
		assert parse_sel_fpr_bid(resp) == 299500.0

	def test_field_missing_returns_zero(self):
		"""sel_fpr_bid 필드 부재 → 0.0."""
		from api.check_bid import parse_sel_fpr_bid
		assert parse_sel_fpr_bid({}) == 0.0
		assert parse_sel_fpr_bid({'other_field': 'X'}) == 0.0

	def test_empty_string_returns_zero(self):
		"""빈 문자열 → 0.0."""
		from api.check_bid import parse_sel_fpr_bid
		assert parse_sel_fpr_bid({'sel_fpr_bid': ''}) == 0.0

	def test_none_returns_zero(self):
		"""None → 0.0."""
		from api.check_bid import parse_sel_fpr_bid
		assert parse_sel_fpr_bid({'sel_fpr_bid': None}) == 0.0

	def test_invalid_string_returns_zero(self):
		"""숫자 변환 실패 → 0.0 (안전 폴백)."""
		from api.check_bid import parse_sel_fpr_bid
		assert parse_sel_fpr_bid({'sel_fpr_bid': 'abc'}) == 0.0

	def test_non_dict_input_returns_zero(self):
		"""dict 아닌 입력 → 0.0 (안전망)."""
		from api.check_bid import parse_sel_fpr_bid
		assert parse_sel_fpr_bid(None) == 0.0
		assert parse_sel_fpr_bid("string") == 0.0
		assert parse_sel_fpr_bid([]) == 0.0

	def test_integer_input(self):
		"""int로 직접 전달돼도 처리."""
		from api.check_bid import parse_sel_fpr_bid
		assert parse_sel_fpr_bid({'sel_fpr_bid': 299500}) == 299500.0
