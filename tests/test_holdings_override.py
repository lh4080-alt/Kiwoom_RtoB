"""holdings.get_holding_override (Feature 2 per-stock tpr/slr) 단위 테스트."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


@pytest.fixture
def tmp_holdings(tmp_path, monkeypatch):
	"""holdings 경로 격리 + 캐시 리셋."""
	from utils import holdings as h
	tmp_file = tmp_path / 'holdings.json'
	monkeypatch.setattr(h, '_HOLDINGS_PATH', str(tmp_file))
	# 캐시 강제 리셋 — 테스트 간 격리
	h._override_cache['data'] = None
	h._override_cache['time'] = 0.0
	return tmp_file


class TestHoldingOverride:

	def test_no_holdings_returns_none(self, tmp_holdings):
		from utils.holdings import get_holding_override
		assert get_holding_override('005930') == {'tpr': None, 'slr': None}

	def test_pick_holding_no_override(self, tmp_holdings):
		"""pick 보유 종목 — tpr/slr 필드 부재 → None 반환 (글로벌 fallback)."""
		from utils.holdings import add_holding, get_holding_override

		async def _setup():
			await add_holding({'code': '005930', 'buy_price': 60000, 'source': 'pick'})

		asyncio.run(_setup())
		assert get_holding_override('005930') == {'tpr': None, 'slr': None}

	def test_stick_holding_with_override(self, tmp_holdings):
		"""stick 보유 종목 — tpr/slr 값 반환."""
		from utils.holdings import add_holding, get_holding_override

		async def _setup():
			await add_holding({
				'code': '122630', 'buy_price': 12500,
				'source': 'stick', 'tpr': 3.0, 'slr': -2.0,
			})

		asyncio.run(_setup())
		ov = get_holding_override('122630')
		assert ov == {'tpr': 3.0, 'slr': -2.0}

	def test_partial_override_tpr_only(self, tmp_holdings):
		"""tpr만 override (slr는 글로벌)."""
		from utils.holdings import add_holding, get_holding_override

		async def _setup():
			await add_holding({'code': '122630', 'source': 'stick', 'tpr': 3.0})

		asyncio.run(_setup())
		ov = get_holding_override('122630')
		assert ov == {'tpr': 3.0, 'slr': None}

	def test_unknown_code_returns_none(self, tmp_holdings):
		"""등록 안 된 종목 — None."""
		from utils.holdings import add_holding, get_holding_override

		async def _setup():
			await add_holding({'code': '005930', 'source': 'pick'})

		asyncio.run(_setup())
		assert get_holding_override('999999') == {'tpr': None, 'slr': None}

	def test_cache_invalidation(self, tmp_holdings, monkeypatch):
		"""캐시 만료 후 새 데이터 읽음."""
		from utils import holdings
		from utils.holdings import add_holding, get_holding_override

		async def _add(entry):
			await add_holding(entry)

		# 캐시 만료를 위해 cache_sec=0 사용
		asyncio.run(_add({'code': '005930', 'source': 'pick'}))
		ov1 = get_holding_override('005930', cache_sec=0)
		assert ov1 == {'tpr': None, 'slr': None}

		# 새 stick entry 추가
		asyncio.run(_add({'code': '122630', 'source': 'stick', 'tpr': 3.0, 'slr': -2.0}))
		ov2 = get_holding_override('122630', cache_sec=0)
		assert ov2 == {'tpr': 3.0, 'slr': -2.0}
