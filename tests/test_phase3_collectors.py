"""semi_trigger Phase 3 — FX + 외인 5일 누적 단위 테스트."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestParseSigned:

	def test_positive(self):
		from modules.semi_trigger.collectors.foreign_flow import _parse_signed
		assert _parse_signed('12345') == 12345
		assert _parse_signed('+12345') == 12345

	def test_single_minus(self):
		from modules.semi_trigger.collectors.foreign_flow import _parse_signed
		assert _parse_signed('-12345') == -12345

	def test_double_minus(self):
		"""키움 일부 TR (ka90013) 응답에서 이중 마이너스 형식."""
		from modules.semi_trigger.collectors.foreign_flow import _parse_signed
		assert _parse_signed('--12345') == -12345

	def test_empty_none(self):
		from modules.semi_trigger.collectors.foreign_flow import _parse_signed
		assert _parse_signed(None) == 0
		assert _parse_signed('') == 0
		assert _parse_signed('   ') == 0

	def test_invalid(self):
		from modules.semi_trigger.collectors.foreign_flow import _parse_signed
		assert _parse_signed('abc') == 0
		assert _parse_signed('1.5') == 1  # float → int


class TestAggregateForeignFlow5d:

	def test_normal_5day_sum(self):
		"""5일 외인 매수 (천주) × 1000 × 종가 = 원."""
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		items = [
			{'frgnr_invsr': '100'},  # 최신
			{'frgnr_invsr': '-50'},
			{'frgnr_invsr': '200'},
			{'frgnr_invsr': '300'},
			{'frgnr_invsr': '-100'},
		]
		# total_qty = 100 - 50 + 200 + 300 - 100 = 450 (천주)
		# total_won = 450 × 1000 × 50000 = 22,500,000,000원
		r = aggregate_foreign_flow_5d(items, close_price=50000)
		assert r == 22_500_000_000

	def test_only_first_5_used(self):
		"""6일 이상 데이터 있어도 첫 5일만."""
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		items = [{'frgnr_invsr': '100'}] * 10
		# 5 × 100 = 500
		# 500 × 1000 × 1000 = 500,000,000
		r = aggregate_foreign_flow_5d(items, close_price=1000)
		assert r == 500_000_000

	def test_custom_days(self):
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		items = [{'frgnr_invsr': '50'}] * 10
		# 3일 × 50 = 150 (천주)
		r = aggregate_foreign_flow_5d(items, close_price=1000, days=3)
		assert r == 150_000_000  # 150 × 1000 × 1000

	def test_empty_items(self):
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		assert aggregate_foreign_flow_5d([], close_price=50000) == 0

	def test_zero_close(self):
		"""종가 0 → 0 반환 (가격 정보 없으면 원 환산 불가)."""
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		assert aggregate_foreign_flow_5d([{'frgnr_invsr': '100'}], close_price=0) == 0
		assert aggregate_foreign_flow_5d([{'frgnr_invsr': '100'}], close_price=-1) == 0

	def test_net_sell_negative(self):
		"""순매도 음수 부호 유지."""
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		items = [
			{'frgnr_invsr': '-1000'},
			{'frgnr_invsr': '-500'},
			{'frgnr_invsr': '100'},
		]
		# total_qty = -1400 (천주)
		# total_won = -1400 × 1000 × 50000 = -70,000,000,000원
		r = aggregate_foreign_flow_5d(items, close_price=50000)
		assert r == -70_000_000_000

	def test_double_minus_in_response(self):
		"""키움 응답에 '--12345' 같이 이중 마이너스 들어와도 부호 정상 처리."""
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		items = [{'frgnr_invsr': '--500'}]
		# parse: -500. total_won = -500 × 1000 × 1000 = -500,000,000
		r = aggregate_foreign_flow_5d(items, close_price=1000)
		assert r == -500_000_000

	def test_real_005930_5_27_data(self):
		"""5/27 삼성전자 데이터 sanity (외인 +24,091천주 × 종가 307,000원 예시)."""
		from modules.semi_trigger.collectors.foreign_flow import aggregate_foreign_flow_5d
		items = [
			{'frgnr_invsr': '24091'},   # 5/27 외인 매수 (예시)
			{'frgnr_invsr': '-5000'},
			{'frgnr_invsr': '8000'},
			{'frgnr_invsr': '12000'},
			{'frgnr_invsr': '-3000'},
		]
		# total_qty = 24091 - 5000 + 8000 + 12000 - 3000 = 36091 천주
		# total_won = 36091 × 1000 × 307000 = 11,079,937,000,000원 (약 11조원)
		r = aggregate_foreign_flow_5d(items, close_price=307000)
		assert r == 36091 * 1000 * 307000
