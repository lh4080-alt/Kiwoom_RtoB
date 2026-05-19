"""pytest 공용 fixtures."""
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def temp_daily_dir():
	"""임시 daily CSV 디렉토리. 테스트 종료 후 자동 정리."""
	d = Path(tempfile.mkdtemp())
	yield d
	shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_daily_csv(temp_daily_dir):
	"""정상 daily CSV 1개 생성 (2건 행, code는 6자리 zero-padded str)."""
	csv_path = temp_daily_dir / '2026-05-15.csv'
	df = pd.DataFrame([
		{'eval_date': '2026-05-15', 'code': '005930', 'today_close': 75000,
		 'score': 8, 'd1_close': None, 'd1_return_pct': None,
		 'd5_close': None, 'd5_return_pct': None},
		{'eval_date': '2026-05-15', 'code': '000660', 'today_close': 150000,
		 'score': 6, 'd1_close': None, 'd1_return_pct': None,
		 'd5_close': None, 'd5_return_pct': None},
	])
	df.to_csv(csv_path, index=False)
	return csv_path


@pytest.fixture
def empty_daily_csv(temp_daily_dir):
	"""빈 daily CSV (0 byte) — 운영 중 평가 실패 잔재로 발생 가능."""
	csv_path = temp_daily_dir / '2026-05-19.csv'
	csv_path.touch()
	return csv_path


@pytest.fixture
def header_only_csv(temp_daily_dir):
	"""헤더만 있는 daily CSV (데이터 0건)."""
	csv_path = temp_daily_dir / '2026-05-20.csv'
	df = pd.DataFrame(columns=['eval_date', 'code', 'today_close', 'score'])
	df.to_csv(csv_path, index=False)
	return csv_path
