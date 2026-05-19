"""
일별/주별 손익 추적 (실현 + 평가).
일일 -10% / 주간 -15% 한도 도달 시 매수 정지.

config/data/pnl.json 영속화. capital_base는 settings.json에서 가져옴 (default 10,000,000).

영구 원칙 (메모리 #30): 봇 데몬 내부에서만 조작.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, date
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PNL_PATH = os.path.join(_BASE_DIR, 'config', 'data', 'pnl.json')

_lock = asyncio.Lock()

DAILY_LOSS_LIMIT = -0.10   # -10%
WEEKLY_LOSS_LIMIT = -0.15  # -15%
DEFAULT_CAPITAL_BASE = 10_000_000


def _get_capital_base() -> int:
	try:
		from utils.get_setting import get_setting
		v = get_setting('capital_base', DEFAULT_CAPITAL_BASE)
		return int(v) if v else DEFAULT_CAPITAL_BASE
	except Exception:
		return DEFAULT_CAPITAL_BASE


def _load_sync() -> dict:
	if not os.path.exists(_PNL_PATH):
		return {
			'daily': {},
			'weekly': {},
			'realized_today': 0,
			'last_update': datetime.now().isoformat(timespec='seconds'),
		}
	try:
		with open(_PNL_PATH, 'r', encoding='utf-8') as f:
			data = json.load(f)
		if not isinstance(data, dict):
			return {'daily': {}, 'weekly': {}, 'realized_today': 0,
			        'last_update': datetime.now().isoformat(timespec='seconds')}
		data.setdefault('daily', {})
		data.setdefault('weekly', {})
		data.setdefault('realized_today', 0)
		return data
	except json.JSONDecodeError:
		logger.exception("pnl 파싱 실패")
		return {'daily': {}, 'weekly': {}, 'realized_today': 0,
		        'last_update': datetime.now().isoformat(timespec='seconds')}


def _save_sync(pnl: dict) -> None:
	pnl['last_update'] = datetime.now().isoformat(timespec='seconds')
	os.makedirs(os.path.dirname(_PNL_PATH), exist_ok=True)
	tmp = _PNL_PATH + '.tmp'
	with open(tmp, 'w', encoding='utf-8') as f:
		json.dump(pnl, f, ensure_ascii=False, indent=2)
	os.replace(tmp, _PNL_PATH)


async def load_pnl() -> dict:
	async with _lock:
		return _load_sync()


async def save_pnl(pnl: dict) -> None:
	async with _lock:
		_save_sync(pnl)


async def reset_daily_if_new_day():
	"""오늘 첫 호출 시 realized_today를 0으로 리셋. 봇 startup 또는 자정 호출."""
	async with _lock:
		pnl = _load_sync()
		today = date.today().isoformat()
		last = pnl.get('last_update', '')
		last_date = last[:10] if last else ''
		if last_date != today:
			pnl['realized_today'] = 0
			_save_sync(pnl)
			logger.info(f"[pnl] realized_today reset for {today}")


async def record_realized(amount: int):
	"""매도 완료 시 실현 손익 추가 (amount: 원, 양수=이익, 음수=손실)."""
	async with _lock:
		pnl = _load_sync()
		capital = _get_capital_base()
		pnl['realized_today'] = int(pnl.get('realized_today', 0)) + int(amount)

		today = date.today().isoformat()
		pnl['daily'].setdefault(today, 0.0)
		pnl['daily'][today] = float(pnl['daily'][today]) + (amount / capital)

		week = date.today().strftime('%G-W%V')  # ISO week
		pnl['weekly'].setdefault(week, 0.0)
		pnl['weekly'][week] = float(pnl['weekly'][week]) + (amount / capital)

		_save_sync(pnl)
		logger.info(f"[pnl] 실현 {amount:+,}원 / 오늘 누적 {pnl['realized_today']:+,}원")


async def calc_current_pnl(holdings: list, get_current_price: Callable) -> dict:
	"""실현 + 평가 합산 손익 비율 계산.

	Args:
		holdings: 보유 종목 리스트 (filled status만).
		get_current_price: async callable(code: str) -> int. 0이면 평가에서 제외.

	Returns:
		{realized_today_pct, unrealized_pct, total_today_pct, total_weekly_pct, capital_base}
	"""
	pnl = await load_pnl()
	capital = _get_capital_base()

	unrealized_won = 0
	for h in holdings:
		try:
			current = await get_current_price(h.get('code', ''))
			if not current:
				continue
			diff = (int(current) - int(h.get('buy_price', 0))) * int(h.get('buy_qty', 0))
			unrealized_won += diff
		except Exception:
			logger.exception(f"현재가 조회 실패: {h.get('code')}")

	today = date.today().isoformat()
	week = date.today().strftime('%G-W%V')

	realized_today_pct = float(pnl['daily'].get(today, 0.0))
	unrealized_pct = unrealized_won / capital if capital else 0.0
	total_today_pct = realized_today_pct + unrealized_pct
	total_weekly_pct = float(pnl['weekly'].get(week, 0.0)) + unrealized_pct

	return {
		'realized_today_pct': realized_today_pct,
		'unrealized_pct': unrealized_pct,
		'total_today_pct': total_today_pct,
		'total_weekly_pct': total_weekly_pct,
		'capital_base': capital,
	}


async def check_limits(holdings: list, get_current_price: Callable) -> Optional[str]:
	"""한도 초과 여부.

	Returns:
		None: 정상
		'daily_halt': 일일 -10% 초과
		'weekly_halt': 주간 -15% 초과 (일일보다 더 강한 신호)
	"""
	p = await calc_current_pnl(holdings, get_current_price)
	if p['total_weekly_pct'] <= WEEKLY_LOSS_LIMIT:
		logger.warning(f"[pnl] 주간 한도 초과: {p['total_weekly_pct']:.2%}")
		return 'weekly_halt'
	if p['total_today_pct'] <= DAILY_LOSS_LIMIT:
		logger.warning(f"[pnl] 일일 한도 초과: {p['total_today_pct']:.2%}")
		return 'daily_halt'
	return None
