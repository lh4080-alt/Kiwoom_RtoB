"""ka10081 (주식일봉차트조회) wrapper 단위 테스트 — 응답 파싱 로직."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestAbsHelpers:

	def test_abs_int_basic(self):
		from api.daily_candle import _abs_int
		assert _abs_int('12500') == 12500
		assert _abs_int('-12500') == 12500
		assert _abs_int('+12500') == 12500

	def test_abs_int_none_empty(self):
		from api.daily_candle import _abs_int
		assert _abs_int(None) == 0
		assert _abs_int('') == 0
		assert _abs_int('   ') == 0
		assert _abs_int(None, default=-1) == -1

	def test_abs_int_invalid(self):
		from api.daily_candle import _abs_int
		assert _abs_int('abc') == 0
		assert _abs_int('1.5') == 1   # float → int

	def test_abs_float(self):
		from api.daily_candle import _abs_float
		assert _abs_float('-3.14') == 3.14
		assert _abs_float(None) == 0.0
		assert _abs_float('+0.5') == 0.5


class TestParseDailyCandles:

	def test_parse_normal(self):
		from api.daily_candle import parse_daily_candles
		resp = {
			'return_code': 0,
			'stk_dt_pole_chart_qry': [
				{
					'dt': '20260601',
					'cur_prc': '52295',
					'open_pric': '50000',
					'high_pric': '53000',
					'low_pric': '49500',
					'trde_qty': '120000',
					'trde_prica': '6240000000',
				},
				{
					'dt': '20260529',
					'cur_prc': '49730',
					'open_pric': '49000',
					'high_pric': '50000',
					'low_pric': '48800',
					'trde_qty': '95000',
					'trde_prica': '4724350000',
				},
			]
		}
		out = parse_daily_candles(resp)
		assert len(out) == 2
		assert out[0]['date'] == '20260601'
		assert out[0]['close'] == 52295
		assert out[0]['trade_amount'] == 6240000000
		assert out[1]['date'] == '20260529'
		assert out[1]['close'] == 49730

	def test_parse_negative_close_abs(self):
		"""키움 응답에 음수 부호 '-' 붙은 가격 → abs."""
		from api.daily_candle import parse_daily_candles
		resp = {
			'stk_dt_pole_chart_qry': [{
				'dt': '20260601',
				'cur_prc': '-52295',
				'open_pric': '-50000',
				'trde_prica': '6240000000',
			}]
		}
		out = parse_daily_candles(resp)
		assert out[0]['close'] == 52295
		assert out[0]['open'] == 50000

	def test_parse_empty_list(self):
		from api.daily_candle import parse_daily_candles
		assert parse_daily_candles({'stk_dt_pole_chart_qry': []}) == []
		assert parse_daily_candles({}) == []

	def test_parse_invalid_response(self):
		from api.daily_candle import parse_daily_candles
		assert parse_daily_candles(None) == []
		assert parse_daily_candles('not a dict') == []
		# 리스트가 아닌 경우
		assert parse_daily_candles({'stk_dt_pole_chart_qry': 'oops'}) == []

	def test_parse_skips_invalid_items(self):
		"""dt 없거나 dict 아닌 항목은 스킵."""
		from api.daily_candle import parse_daily_candles
		resp = {
			'stk_dt_pole_chart_qry': [
				{'dt': '20260601', 'cur_prc': '100'},   # valid
				'not a dict',                            # skip
				{'cur_prc': '200'},                      # no dt → skip
				{'dt': '', 'cur_prc': '300'},            # empty dt → skip
				{'dt': '20260531', 'cur_prc': '400'},   # valid
			]
		}
		out = parse_daily_candles(resp)
		assert len(out) == 2
		assert [r['date'] for r in out] == ['20260601', '20260531']

	def test_parse_missing_fields_default_zero(self):
		"""필드 부재 시 0 (int)."""
		from api.daily_candle import parse_daily_candles
		resp = {
			'stk_dt_pole_chart_qry': [{'dt': '20260601'}]
		}
		out = parse_daily_candles(resp)
		assert out[0]['date'] == '20260601'
		assert out[0]['open'] == 0
		assert out[0]['close'] == 0
		assert out[0]['volume'] == 0
		assert out[0]['trade_amount'] == 0
