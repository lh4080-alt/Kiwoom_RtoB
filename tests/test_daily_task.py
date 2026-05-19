"""daily_quality_logger 함수 edge case 테스트.

핵심: 빈 풀, 빈 CSV, 헤더만 CSV 등 운영 중 발생 가능한 edge case에서
EmptyDataError나 잘못된 dtype 추론으로 daily task 전체가 죽지 않아야 함.
"""
import pandas as pd
import pytest

from tools.daily_quality_logger import (
	backfill_returns,
	evaluate_today_pool,
	rebuild_master,
)


class TestEvaluateEmptyPool:
	"""빈 풀 케이스 — CSV 만들지 않아야 함 (다음 read_csv 호출에서 EmptyDataError 방지)."""

	def test_empty_pool_returns_empty_df_no_csv(self, temp_daily_dir, monkeypatch):
		"""풀 0건이면 빈 DataFrame 반환 + CSV 미생성."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		monkeypatch.setattr(
			'tools.daily_quality_logger.load_today_pool_full',
			lambda: {}
		)

		result = evaluate_today_pool('2026-05-19')

		assert result.empty
		assert not (temp_daily_dir / '2026-05-19.csv').exists()

	def test_no_evaluable_stocks_no_csv(self, temp_daily_dir, monkeypatch):
		"""풀은 있지만 모두 데이터 부족 → 평가 결과 0건 → CSV 미생성."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		monkeypatch.setattr(
			'tools.daily_quality_logger.load_today_pool_full',
			lambda: {'999999': {'hit_count': 1}}
		)
		monkeypatch.setattr(
			'tools.daily_quality_logger.load_7d_bars',
			lambda code, end_date=None: None
		)
		monkeypatch.setattr(
			'tools.daily_quality_logger.load_market_change',
			lambda d: (0.0, 0.0)
		)

		result = evaluate_today_pool('2026-05-19')

		assert result.empty
		assert not (temp_daily_dir / '2026-05-19.csv').exists()


class TestBackfillEmptyCSV:
	"""backfill_returns가 빈/헤더만 CSV 만나도 죽지 않아야 함."""

	def test_empty_csv_skipped(self, temp_daily_dir, empty_daily_csv, monkeypatch):
		"""0 byte CSV → EmptyDataError catch → 다음 파일로 진행."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		try:
			backfill_returns('2026-05-19')
		except pd.errors.EmptyDataError:
			pytest.fail("EmptyDataError가 처리되지 않음")

	def test_header_only_csv_no_exception(self, temp_daily_dir, header_only_csv, monkeypatch):
		"""헤더만 있는 CSV → 빈 DataFrame → 정상 처리."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		try:
			backfill_returns('2026-05-20')
		except Exception as e:
			pytest.fail(f"헤더만 CSV에서 예외 발생: {type(e).__name__}: {e}")

	def test_normal_csv_no_exception(self, temp_daily_dir, sample_daily_csv, monkeypatch):
		"""정상 CSV는 예외 없이 처리."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		monkeypatch.setattr(
			'tools.daily_quality_logger.lookup_close',
			lambda code, eval_date, offset_bdays: None
		)
		try:
			backfill_returns('2026-05-15')
		except Exception as e:
			pytest.fail(f"정상 CSV에서 예외: {type(e).__name__}: {e}")


class TestRebuildMasterEdgeCases:
	"""rebuild_master edge case."""

	def test_no_daily_files_returns_empty(self, temp_daily_dir, monkeypatch):
		"""daily 디렉토리에 파일 없으면 빈 DataFrame 반환."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		monkeypatch.setattr(
			'tools.daily_quality_logger.MASTER_CSV',
			temp_daily_dir / 'master.csv'
		)

		result = rebuild_master()
		assert result.empty

	def test_empty_csv_skipped_in_rebuild(self, temp_daily_dir, empty_daily_csv,
	                                       sample_daily_csv, monkeypatch):
		"""빈 CSV + 정상 CSV 섞임 → 빈 CSV skip, 정상만 master에."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		monkeypatch.setattr(
			'tools.daily_quality_logger.MASTER_CSV',
			temp_daily_dir / 'master.csv'
		)

		result = rebuild_master()
		assert len(result) == 2
		assert '005930' in result['code'].values
		assert '000660' in result['code'].values

	def test_code_column_stays_string(self, temp_daily_dir, sample_daily_csv, monkeypatch):
		"""rebuild 후 code 컬럼이 str 유지 (int 추론 X — 5/18 사고 재현 방지)."""
		monkeypatch.setattr('tools.daily_quality_logger.DAILY_DIR', temp_daily_dir)
		monkeypatch.setattr(
			'tools.daily_quality_logger.MASTER_CSV',
			temp_daily_dir / 'master.csv'
		)

		result = rebuild_master()
		assert result['code'].dtype == object  # pandas object = str
		assert all(isinstance(c, str) for c in result['code'])
		assert all(len(c) == 6 for c in result['code'])  # 6자리 zero-padded
