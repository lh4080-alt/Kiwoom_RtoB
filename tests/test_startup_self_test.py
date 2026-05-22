"""startup self-test 필드 점검 — 5/22 사고 후 추가 안전망 검증.

ka10001 응답에서 봇이 의존하는 핵심 필드(cur_prc, base_pric, stk_nm) 누락을
봇 시작 시점에 감지하기 위한 헬퍼.
"""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestCheckKa10001Fields:

	def test_all_fields_present(self):
		"""실제 키움 응답 형태 — 필드 모두 있음 → 빈 리스트."""
		from modules.startup_self_test import check_ka10001_fields
		resp = {
			'stk_cd': '005930', 'stk_nm': '삼성전자',
			'cur_prc': '-294000', 'base_pric': '299500',
			'pred_pre': '-5500',
		}
		assert check_ka10001_fields(resp) == []

	def test_base_pric_missing(self):
		"""base_pric 누락 → 사고 재현 가능 상태 감지."""
		from modules.startup_self_test import check_ka10001_fields
		resp = {'stk_nm': '삼성전자', 'cur_prc': '-294000'}
		missing = check_ka10001_fields(resp)
		assert 'base_pric' in missing

	def test_all_three_missing(self):
		"""모든 필드 누락 → 3건 모두 보고."""
		from modules.startup_self_test import check_ka10001_fields
		missing = check_ka10001_fields({})
		assert set(missing) == {'cur_prc', 'base_pric', 'stk_nm'}

	def test_empty_string_treated_as_missing(self):
		"""빈 문자열도 누락 처리 (키움이 ''로 응답하는 케이스 대비)."""
		from modules.startup_self_test import check_ka10001_fields
		resp = {'stk_nm': '삼성전자', 'cur_prc': '-294000', 'base_pric': ''}
		assert 'base_pric' in check_ka10001_fields(resp)

	def test_none_treated_as_missing(self):
		"""None도 누락 처리."""
		from modules.startup_self_test import check_ka10001_fields
		resp = {'stk_nm': '삼성전자', 'cur_prc': '-294000', 'base_pric': None}
		assert 'base_pric' in check_ka10001_fields(resp)
