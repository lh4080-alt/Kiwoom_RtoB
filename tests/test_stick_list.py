"""stick_list 영속화 단위 테스트."""
import asyncio
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


@pytest.fixture
def tmp_stick(tmp_path, monkeypatch):
	"""stick_list 경로를 임시 디렉터리로 격리."""
	from utils import stick_list as sl
	tmp_file = tmp_path / 'stick_list.json'
	monkeypatch.setattr(sl, '_STICK_PATH', str(tmp_file))
	return tmp_file


class TestAddStick:

	def test_basic_add(self, tmp_stick):
		from utils.stick_list import add_stick, load_stick

		async def _run():
			ok = await add_stick('122630', qty=5)
			items = await load_stick()
			return ok, items

		ok, items = asyncio.run(_run())
		assert ok is True
		assert len(items) == 1
		assert items[0]['code'] == '122630'
		assert items[0]['qty'] == 5
		assert 'tpr' not in items[0]
		assert 'slr' not in items[0]

	def test_with_tpr_slr(self, tmp_stick):
		from utils.stick_list import add_stick, load_stick

		async def _run():
			await add_stick('122630', qty=5, tpr=3, slr=2)
			return await load_stick()

		items = asyncio.run(_run())
		assert items[0]['tpr'] == 3.0
		# 양수 입력 → 음수 변환
		assert items[0]['slr'] == -2.0

	def test_duplicate_returns_false(self, tmp_stick):
		from utils.stick_list import add_stick

		async def _run():
			r1 = await add_stick('122630', qty=5)
			r2 = await add_stick('122630', qty=10)
			return r1, r2

		r1, r2 = asyncio.run(_run())
		assert r1 is True
		assert r2 is False

	def test_invalid_qty_clamps_to_1(self, tmp_stick):
		from utils.stick_list import add_stick, load_stick

		async def _run():
			await add_stick('122630', qty=0)
			return await load_stick()

		items = asyncio.run(_run())
		assert items[0]['qty'] == 1

	def test_negative_slr_input_stays_negative(self, tmp_stick):
		"""음수로 입력해도 abs 처리 후 음수 변환."""
		from utils.stick_list import add_stick, load_stick

		async def _run():
			await add_stick('122630', qty=5, slr=-2.5)
			return await load_stick()

		items = asyncio.run(_run())
		assert items[0]['slr'] == -2.5


class TestRemoveStick:

	def test_remove_existing(self, tmp_stick):
		from utils.stick_list import add_stick, remove_stick, load_stick

		async def _run():
			await add_stick('122630', qty=5)
			ok = await remove_stick('122630')
			items = await load_stick()
			return ok, items

		ok, items = asyncio.run(_run())
		assert ok is True
		assert items == []

	def test_remove_missing_returns_false(self, tmp_stick):
		from utils.stick_list import remove_stick

		async def _run():
			return await remove_stick('999999')

		assert asyncio.run(_run()) is False


class TestFindStick:

	def test_find_existing(self):
		from utils.stick_list import find_stick
		items = [{'code': '122630'}, {'code': '233740'}]
		assert find_stick(items, '233740') == {'code': '233740'}

	def test_find_missing(self):
		from utils.stick_list import find_stick
		assert find_stick([{'code': '122630'}], '999999') is None

	def test_find_in_empty(self):
		from utils.stick_list import find_stick
		assert find_stick([], '122630') is None
