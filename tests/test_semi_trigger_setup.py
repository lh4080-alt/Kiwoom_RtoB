"""semi_trigger Phase 0a — ETF 매핑 + DB 스키마 단위 테스트."""
import sys
from pathlib import Path

import pytest

_AUTOMATION = Path(__file__).parent.parent / 'automation'
if str(_AUTOMATION) not in sys.path:
	sys.path.insert(0, str(_AUTOMATION))


class TestEtfMapping:

	def test_14_etfs_registered(self):
		from modules.semi_trigger.etf_mapping import ETF_TO_UNDERLYING
		assert len(ETF_TO_UNDERLYING) == 14

	def test_split_7_per_underlying(self):
		"""삼성전자 7종 + SK하이닉스 7종."""
		from modules.semi_trigger.etf_mapping import (
			ETF_TO_UNDERLYING, UNDERLYING_TO_ETFS,
		)
		samsung_etfs = [e for e, u in ETF_TO_UNDERLYING.items() if u == '005930']
		hynix_etfs = [e for e, u in ETF_TO_UNDERLYING.items() if u == '000660']
		assert len(samsung_etfs) == 7
		assert len(hynix_etfs) == 7
		assert len(UNDERLYING_TO_ETFS['005930']) == 7
		assert len(UNDERLYING_TO_ETFS['000660']) == 7

	def test_get_etfs_for_underlying(self):
		from modules.semi_trigger.etf_mapping import get_etfs_for_underlying
		etfs = get_etfs_for_underlying('005930')
		assert len(etfs) == 7
		assert '491220' in etfs   # KODEX 삼성전자단일종목레버리지

	def test_get_underlying(self):
		from modules.semi_trigger.etf_mapping import get_underlying
		assert get_underlying('491220') == '005930'
		assert get_underlying('491230') == '000660'
		assert get_underlying('999999') == ''  # 미등록

	def test_target_underlyings(self):
		"""대상은 005930 + 000660 두 종목."""
		from modules.semi_trigger.etf_mapping import TARGET_UNDERLYINGS
		assert TARGET_UNDERLYINGS == ('005930', '000660')

	def test_no_duplicate_etf_codes(self):
		"""중복 ETF 코드 없음."""
		from modules.semi_trigger.etf_mapping import ETF_TO_UNDERLYING
		assert len(ETF_TO_UNDERLYING) == len(set(ETF_TO_UNDERLYING.keys()))

	def test_etf_codes_are_6_digits(self):
		"""모든 ETF 코드는 6자리 숫자 문자열."""
		from modules.semi_trigger.etf_mapping import ETF_TO_UNDERLYING
		for code in ETF_TO_UNDERLYING:
			assert isinstance(code, str)
			assert len(code) == 6
			assert code.isdigit()


@pytest.fixture
def tmp_db(tmp_path):
	from modules.semi_trigger import db as db_mod
	tmp_file = str(tmp_path / 'semi_trigger_test.db')
	db_mod._initialized = False
	db_mod.init_db(tmp_file)
	return tmp_file


class TestSchema:

	def test_init_creates_tables(self, tmp_db):
		from modules.semi_trigger.db import connect
		with connect(tmp_db) as conn:
			rows = conn.execute(
				"SELECT name FROM sqlite_master WHERE type='table' "
				"ORDER BY name"
			).fetchall()
			names = [r['name'] for r in rows]
			assert 'daily_factors' in names
			assert 'scores' in names

	def test_init_is_idempotent(self, tmp_db):
		from modules.semi_trigger.db import init_db
		init_db(tmp_db)
		init_db(tmp_db)  # no error


class TestUpsertFactors:

	def test_insert_then_replace(self, tmp_db):
		"""부분 upsert — 두 번째 호출에서 누락된 키는 기존 값 유지 (6/2 변경)."""
		from modules.semi_trigger.db import upsert_factors, fetch_recent_factors
		upsert_factors('2026-06-01', '005930',
		               {'us_memory': 1.5, 'etf_flow': 100.0}, db_path=tmp_db)
		# 부분 update — us_memory 덮어쓰고 mu 추가, etf_flow는 기존 값 유지
		upsert_factors('2026-06-01', '005930',
		               {'us_memory': 2.0, 'mu': 3.0}, db_path=tmp_db)
		rows = fetch_recent_factors('005930', n=10, db_path=tmp_db)
		assert len(rows) == 1
		assert rows[0]['us_memory'] == 2.0
		assert rows[0]['mu'] == 3.0
		# etf_flow는 기존 값 유지 (병합)
		assert rows[0]['etf_flow'] == 100.0

	def test_fetch_recent_order(self, tmp_db):
		from modules.semi_trigger.db import upsert_factors, fetch_recent_factors
		for d in ['2026-05-29', '2026-06-01', '2026-05-30']:
			upsert_factors(d, '005930', {'us_memory': 1.0}, db_path=tmp_db)
		rows = fetch_recent_factors('005930', n=10, db_path=tmp_db)
		# date DESC
		assert [r['date'] for r in rows] == ['2026-06-01', '2026-05-30', '2026-05-29']

	def test_fetch_recent_limit(self, tmp_db):
		from modules.semi_trigger.db import upsert_factors, fetch_recent_factors
		for i in range(25):
			upsert_factors(f'2026-05-{(i % 28) + 1:02d}', '005930',
			               {'us_memory': float(i)}, db_path=tmp_db)
		# 동일 키 다수 → 한 row로 압축됨. 새로 5개 날짜 추가
		for d in ['2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04', '2026-06-05']:
			upsert_factors(d, '005930', {'us_memory': 9.0}, db_path=tmp_db)
		rows = fetch_recent_factors('005930', n=3, db_path=tmp_db)
		assert len(rows) == 3


class TestUpsertScore:

	def test_score_insert_and_fetch(self, tmp_db):
		from modules.semi_trigger.db import upsert_score, fetch_score
		upsert_score('2026-06-01', '005930', {
			'us_memory_z': 1.5, 'etf_flow_z': 2.0,
			'fx_z': 0.3, 'foreign_flow_z': 0.8, 'memory_price_z': 0.1,
			'semi_score': 1.15, 'trigger': 1, 'legacy_trigger': 0,
			'baseline_days': 40, 'weight_redistributed': 0,
		}, db_path=tmp_db)
		row = fetch_score('2026-06-01', '005930', db_path=tmp_db)
		assert row is not None
		assert row['semi_score'] == 1.15
		assert row['trigger'] == 1
		assert row['legacy_trigger'] == 0

	def test_score_missing_returns_none(self, tmp_db):
		from modules.semi_trigger.db import fetch_score
		assert fetch_score('2099-01-01', '999999', db_path=tmp_db) is None
