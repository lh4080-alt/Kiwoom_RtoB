"""kt10000 응답 ord_no 추출 — 5/26 실 응답 raw로 정답 키 확정.

실제 키움 응답 형태:
  {"ord_no": "0411161", "dmst_stex_tp": "KRX", "return_code": 0, "return_msg": "..."}

봇 추정 옛 5키 (output.ODNO, ODNO, odno, order_no, orderNo) 모두 미스였음.
실제 키는 최상위 'ord_no' (소문자+언더스코어).
"""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestExtractOrderNo:

	def test_actual_kt10000_response(self):
		"""5/26 buy_command raw로 확인된 실 응답 형태 — 정답 키 추출."""
		from api.buy_stock import extract_order_no
		resp = {
			"ord_no": "0411161",
			"dmst_stex_tp": "KRX",
			"return_code": 0,
			"return_msg": "KRX 매수주문이 완료되었습니다.",
		}
		assert extract_order_no(resp) == "0411161"

	def test_priority_ord_no_over_legacy(self):
		"""'ord_no'가 있으면 옛 키보다 우선."""
		from api.buy_stock import extract_order_no
		resp = {
			"ord_no": "REAL",
			"ODNO": "LEGACY",
			"odno": "LEGACY2",
		}
		assert extract_order_no(resp) == "REAL"

	def test_legacy_odno_fallback(self):
		"""'ord_no' 없고 'ODNO'만 있는 응답 — 폴백."""
		from api.buy_stock import extract_order_no
		resp = {"ODNO": "FALLBACK1", "return_code": 0}
		assert extract_order_no(resp) == "FALLBACK1"

	def test_output_wrap_legacy(self):
		"""output 래핑 응답 — 안전망 폴백."""
		from api.buy_stock import extract_order_no
		resp = {"output": {"ODNO": "WRAPPED"}, "return_code": 0}
		assert extract_order_no(resp) == "WRAPPED"

	def test_no_order_no_returns_none(self):
		"""주문번호 필드 다 없으면 None."""
		from api.buy_stock import extract_order_no
		resp = {"return_code": 0, "return_msg": "..."}
		assert extract_order_no(resp) is None

	def test_non_dict_returns_none(self):
		from api.buy_stock import extract_order_no
		assert extract_order_no(None) is None
		assert extract_order_no("string") is None
		assert extract_order_no([]) is None
