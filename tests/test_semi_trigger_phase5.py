"""semi_trigger Phase 5 — pipeline 통합 + legacy_trigger 단위 테스트."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestCalcLegacyTrigger:
	"""SOX/NVDA/MU 2/3 이상 +0.3%↑ 재현 (기존 stick 룰)."""

	def test_all_above(self):
		from modules.semi_trigger.scoring import calc_legacy_trigger
		assert calc_legacy_trigger(0.5, 0.4, 0.6) == 1

	def test_two_of_three(self):
		from modules.semi_trigger.scoring import calc_legacy_trigger
		assert calc_legacy_trigger(0.5, 0.4, 0.1) == 1

	def test_one_only_fails(self):
		from modules.semi_trigger.scoring import calc_legacy_trigger
		assert calc_legacy_trigger(0.5, 0.1, 0.1) == 0

	def test_exact_threshold(self):
		"""정확히 0.3% = 통과 (>= 비교)."""
		from modules.semi_trigger.scoring import calc_legacy_trigger
		assert calc_legacy_trigger(0.3, 0.3, 0.1) == 1

	def test_none_as_not_rising(self):
		"""None = 상승 아님 (보수적)."""
		from modules.semi_trigger.scoring import calc_legacy_trigger
		assert calc_legacy_trigger(0.5, None, None) == 0
		assert calc_legacy_trigger(0.5, 0.4, None) == 1  # 2/3 통과

	def test_5_27_actual_data(self):
		"""5/27 실제: SOX=0.00%, NVDA=-1.41%, MU=+3.75% → 1/3 미달 → 0."""
		from modules.semi_trigger.scoring import calc_legacy_trigger
		assert calc_legacy_trigger(0.00, -1.41, 3.75) == 0


@pytest.fixture
def tmp_db(tmp_path):
	"""Phase 5 통합 — 임시 DB."""
	from modules.semi_trigger import db as db_mod
	tmp_file = str(tmp_path / 'phase5.db')
	db_mod._initialized = False
	db_mod.init_db(tmp_file)
	return tmp_file


class TestCalcAxesZscores:

	def test_baseline_insufficient(self, tmp_db):
		"""DB 비어있으면 baseline_days=0 → 모든 z=None."""
		from modules.semi_trigger.pipeline import calc_axes_zscores
		z, days = calc_axes_zscores('2026-06-01', '005930', {
			'us_memory': 2.0, 'etf_flow': 100.0, 'fx_change': 0.5,
			'foreign_flow_5d': 1000000, 'memory_price': None,
		}, db_path=tmp_db)
		assert days == 0
		# baseline < 2 → 모든 z None
		assert all(v is None for v in z.values())

	def test_baseline_with_data(self, tmp_db):
		"""과거 데이터 입력 후 z-score 산출."""
		from modules.semi_trigger.pipeline import calc_axes_zscores
		from modules.semi_trigger.db import upsert_factors

		# 과거 5일 입력 (충분치 않지만 z-score는 계산됨)
		for i, d in enumerate(['2026-05-25', '2026-05-26', '2026-05-27', '2026-05-28', '2026-05-29']):
			upsert_factors(d, '005930', {
				'us_memory': 1.0 + i * 0.5,  # 1, 1.5, 2, 2.5, 3 → mean=2, std=0.79..
			}, db_path=tmp_db)

		# 오늘 us_memory = 5.0
		z, days = calc_axes_zscores('2026-06-01', '005930', {
			'us_memory': 5.0,
		}, db_path=tmp_db)
		assert days == 5
		assert z['us_memory'] is not None
		assert z['us_memory'] > 0  # 5.0 > mean(2.0) → positive z

	def test_excludes_today_from_baseline(self, tmp_db):
		"""오늘 데이터는 baseline에서 제외."""
		from modules.semi_trigger.pipeline import calc_axes_zscores
		from modules.semi_trigger.db import upsert_factors

		# 어제 + 오늘 — 오늘은 baseline에서 빠져야 함
		upsert_factors('2026-05-31', '005930', {'us_memory': 1.0}, db_path=tmp_db)
		upsert_factors('2026-06-01', '005930', {'us_memory': 9.0}, db_path=tmp_db)

		z, days = calc_axes_zscores('2026-06-01', '005930', {
			'us_memory': 9.0,
		}, db_path=tmp_db)
		assert days == 1  # 어제만 baseline → calc_zscore baseline<2 → None
		assert z['us_memory'] is None


class TestPipelineIntegration:

	def test_pipeline_with_mocks(self, tmp_db, tmp_path, monkeypatch):
		"""pipeline 통합 — collectors mock + 최소 1회 실행."""
		from modules.semi_trigger import pipeline

		# 1) 모든 collector mock
		async def _mock_us_memory():
			return {'us_memory': 2.0, 'symbols': {}, 'fetched_count': 4}

		async def _mock_etf_flows(base_dt, token):
			return {
				'005930': {'etf_flow': 1_000_000_000, 'etfs_count': 7, 'etfs': []},
				'000660': {'etf_flow': 500_000_000, 'etfs_count': 7, 'etfs': []},
				'fetched_count': 14,
			}

		async def _mock_fx():
			return {'fx_change': 0.5, 'symbol': 'KRW=X'}

		async def _mock_foreign(stock_code, base_dt, token):
			return 1_000_000_000 if stock_code == '005930' else 500_000_000

		async def _mock_memory_price():
			return {'memory_price': None, 'source': None, 'is_carry_forward': False}

		async def _mock_fetch_change_pct(symbol):
			return {'^SOX': 0.0, 'NVDA': -1.0, 'MU': 1.5}.get(symbol, 0.0)

		monkeypatch.setattr(pipeline, 'collect_us_memory', _mock_us_memory)
		monkeypatch.setattr(pipeline, 'collect_etf_flows', _mock_etf_flows)
		monkeypatch.setattr(pipeline, 'collect_fx_change', _mock_fx)
		monkeypatch.setattr(pipeline, 'collect_foreign_flow_5d', _mock_foreign)
		monkeypatch.setattr(pipeline, 'collect_memory_price', _mock_memory_price)

		# fetch_change_pct는 api.external_index 모듈에 있음 — pipeline.py 안에서 import
		# pipeline 내부에서 import 하므로 sys.modules 패치
		import api.external_index as ei_mod
		monkeypatch.setattr(ei_mod, 'fetch_change_pct', _mock_fetch_change_pct)

		json_path = str(tmp_path / 'daily_semi_trigger.json')

		# 2) 첫 실행 — baseline 0
		output = asyncio.run(pipeline.run_pipeline(
			'2026-06-01', token='fake', db_path=tmp_db, json_path=json_path,
		))

		assert output['date'] == '2026-06-01'
		assert output['mode'] == 'shadow'
		assert len(output['targets']) == 2  # 005930 + 000660

		for t in output['targets']:
			# baseline 0 → trigger 보류
			assert t['baseline_days'] == 0
			assert t['baseline_sufficient'] is False
			assert t['trigger'] is False
			# legacy_trigger: SOX 0%, NVDA -1%, MU 1.5% → MU만 1/3 → False
			assert t['legacy_trigger'] is False

		# 3) JSON 출력 확인
		with open(json_path, encoding='utf-8') as f:
			data = json.load(f)
		assert data['date'] == '2026-06-01'
		assert 'params' in data and 'weights' in data['params']
		assert abs(sum(data['params']['weights'].values()) - 1.0) < 1e-9
