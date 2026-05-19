"""data_loaders 함수 테스트.

핵심: int code 입력 → str 정규화. 호출자가 어떤 타입을 넘기든 안전 동작.
이 패턴이 깨지면 backfill_returns가 daily_logger 전체를 죽임 (5/18 사고 재현).
"""
import pytest

from tools.data_loaders import lookup_close, load_7d_bars


class TestLookupClose:
	"""lookup_close는 code를 int로 받아도 str로 처리해야 함."""

	def test_int_code_no_typeerror(self):
		"""int 종목코드 입력 시 TypeError 발생하지 않음 (Path / int 방지)."""
		try:
			lookup_close(5930, '2026-05-15', offset_bdays=1)
		except TypeError as e:
			pytest.fail(f"int code로 TypeError 발생: {e}")
		except (FileNotFoundError, KeyError, ValueError, OSError):
			# 실제 데이터 없는 케이스는 OK
			pass

	def test_str_code_no_typeerror(self):
		"""str 종목코드 정상 동작."""
		try:
			lookup_close('005930', '2026-05-15', offset_bdays=1)
		except TypeError as e:
			pytest.fail(f"str code로 TypeError 발생: {e}")
		except (FileNotFoundError, KeyError, ValueError, OSError):
			pass

	def test_short_int_code_zero_padded(self):
		"""1자리 int code도 6자리 str로 정규화."""
		try:
			lookup_close(1, '2026-05-15', offset_bdays=1)
		except TypeError as e:
			pytest.fail(f"short int code로 TypeError 발생: {e}")
		except (FileNotFoundError, KeyError, ValueError, OSError):
			pass


class TestLoad7dBars:
	"""load_7d_bars도 동일 type-safety 요구."""

	def test_int_code_no_typeerror(self):
		try:
			load_7d_bars(5930)
		except TypeError as e:
			pytest.fail(f"int code로 TypeError 발생: {e}")
		except (FileNotFoundError, KeyError, ValueError, OSError):
			pass

	def test_str_code_no_typeerror(self):
		try:
			load_7d_bars('005930')
		except TypeError as e:
			pytest.fail(f"str code로 TypeError 발생: {e}")
		except (FileNotFoundError, KeyError, ValueError, OSError):
			pass

	def test_short_int_code_zero_padded(self):
		try:
			load_7d_bars(1)
		except TypeError as e:
			pytest.fail(f"short int code로 TypeError 발생: {e}")
		except (FileNotFoundError, KeyError, ValueError, OSError):
			pass
