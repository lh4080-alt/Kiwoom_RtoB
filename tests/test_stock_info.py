"""ka10001 응답에서 전일 종가 추출 — 5/22 GST/HL만도 사고 회피 검증.

키움 실제 응답 필드는 'base_pric'. 봇이 옛 필드명(pred_close_pric 등)만 찾아서
prev_close=0 → failed_no_price 사고를 두 번 일으켰음.
"""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestExtractPrevClose:

	def test_base_pric_normal(self):
		"""키움 실제 응답 — base_pric에 전일 종가 정상 추출."""
		from api.stock_info import extract_prev_close
		resp = {'base_pric': '299500', 'cur_prc': '-294000'}
		assert extract_prev_close(resp) == 299500.0

	def test_base_pric_negative_sign(self):
		"""음수 표기 절댓값 처리."""
		from api.stock_info import extract_prev_close
		resp = {'base_pric': '-299500'}
		assert extract_prev_close(resp) == 299500.0

	def test_legacy_field_fallback(self):
		"""base_pric 없으면 legacy 필드 폴백."""
		from api.stock_info import extract_prev_close
		resp = {'prdy_clpr': '70000'}
		assert extract_prev_close(resp) == 70000.0

	def test_all_missing_returns_zero(self):
		"""모든 필드 None → 0.0 (failed_no_price로 이어짐, 의도된 안전장치)."""
		from api.stock_info import extract_prev_close
		assert extract_prev_close({}) == 0.0
		assert extract_prev_close({'cur_prc': '294000'}) == 0.0

	def test_base_pric_priority_over_legacy(self):
		"""base_pric과 legacy 둘 다 있으면 base_pric 우선."""
		from api.stock_info import extract_prev_close
		resp = {'base_pric': '299500', 'pred_close_pric': '50000'}
		assert extract_prev_close(resp) == 299500.0

	def test_5_22_actual_response(self):
		"""5/22 사고 시점 실제 키움 응답 형태 — base_pric 있으면 정상, 없으면 사고 재현."""
		from api.stock_info import extract_prev_close
		# 실제 키움 응답에서 추출한 형태
		actual = {
			'stk_cd': '005930', 'stk_nm': '삼성전자',
			'cur_prc': '-294000', 'open_pric': '+300000',
			'high_pric': '+300500', 'low_pric': '-292000',
			'base_pric': '299500',  # ← 전일 종가
			'pred_pre': '-5500',
			# pred_close_pric / prdy_clpr / bfdy_clpr / prev_close_price 모두 부재
		}
		assert extract_prev_close(actual) == 299500.0

		# 만약 봇이 base_pric을 무시했다면 (옛 코드) → 0
		legacy_only = {k: v for k, v in actual.items() if k != 'base_pric'}
		assert extract_prev_close(legacy_only) == 0.0
