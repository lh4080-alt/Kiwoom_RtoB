"""거시 모니터 (macro_monitor) — 섹터×거시 일별 관찰 (2026-08-31 Lee 구상).

목적: 코스피 지수 ETF 매매 판단을 위한 장기 관찰 데이터 축적 (P1 단계 — 기록만, 판정 없음).
관찰 축:
  반도체 블록  = 삼성전자(005930) + SK하이닉스(000660) + 소부장ETF(455850) 평균
  기타 섹터    = 13개 대형 섹터 ETF 등락의 동일가중 평균 (아래 ETFS)
  회전 스프레드 = 반도체 블록 등락 − 기타 섹터 등락 (유동성 회전의 직접 관측치)
  거시(전야)   = 나스닥선물 NQ=F · 달러 KRW=X · 미10Y ^TNX (yfinance)
  시장         = 코스피 ^KS11

시차 정렬: 한국 T일 장은 직전 미국 세션(T-1, 미국날짜)의 영향 — 미국 지표는
T-1행을 T일과 짝짓는다 (semi DST 스냅샷과 같은 시간 개념).

데이터 소스:
  국내 주식/ETF — ka10081 일봉(-XMf61 토큰, ~100일) — tokens: semi token_provider 재사용
  거시 — yfinance (semi가 beelink에서 이미 운용 중, 환경 검증됨)

저장: config/data/macro_monitor.jsonl (하루 1줄 append-only, edge_tracking 패턴)
ETF 목록 출처: DCAbot sector_etf_map.yaml (ka10001 실명 검증된 코드만 사용)
  + 해운 441540 / 건설 117700 등은 2026-08-31 별도 ka10001 실명 재검증 완료.
"""
import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# ── 관찰 대상 (ka10001 실명 검증 완료 2026-08-31) ──────────────
SEMIS = [
    ('005930', '삼성전자'),
    ('000660', 'SK하이닉스'),
    ('455850', '소부장ETF'),
]
# 기타 섹터 — 유동성·시총 큰 섹터 중심 (철강 제외, 운송→조선해운, 상사주는 전용 ETF 부재로 제외)
OTHER_ETFS = [
    ('117460', '에너지화학'), ('091170', '은행'), ('102970', '증권'),
    ('091180', '자동차'), ('143860', '헬스케어'), ('305720', '2차전지'),
    ('117700', '건설'), ('487240', 'AI전력'), ('449450', '방산'),
    ('434730', '원자력'), ('494670', '조선TOP10'), ('228790', '화장품'),
    ('441540', '조선해운'),
]
# yfinance 심볼
SYM_NQ = 'NQ=F'
SYM_USDKRW = 'KRW=X'
SYM_US10Y = '^TNX'      # 미 10년물 금리 (×1 = %)
SYM_KOSPI = '^KS11'

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSONL_PATH = os.path.join(BASE_DIR, 'config', 'data', 'macro_monitor.jsonl')

CORR_WINDOW = 60  # 상관 윈도 (거래일)


# ── 국내 수집 (ka10081) ───────────────────────────────────────
async def fetch_kr_daily_closes(token: str, base_dt: str, codes: list) -> dict:
    """codes 각각 ka10081 일봉(~100일) → {code: {date: close}}.

    실패한 코드는 dict에서 누락 (결측 처리는 상류에서).
    """
    from api.daily_candle import fn_ka10081
    out = {}
    for code, _name in codes:
        try:
            resp = await fn_ka10081(code, base_dt=base_dt, token=token, silent=True)
            if resp.get('return_code') != 0:
                logger.warning(f"[macro] ka10081 실패 {code} rc={resp.get('return_code')}")
                continue
            closes = {}
            for c in resp.get('candles', []):
                if c.get('date') and c.get('close'):
                    closes[str(c['date'])] = int(c['close'])
            if closes:
                out[code] = closes
        except Exception:
            logger.exception(f"[macro] ka10081 예외 {code}")
    return out


def _daily_returns(closes: dict) -> dict:
    """{date(YYYYMMDD): close} → {date: ret_pct} (date ASC). 전일 대비 %."""
    dates = sorted(closes.keys())
    out = {}
    for prev, cur in zip(dates, dates[1:]):
        pc = closes[prev]
        if pc > 0:
            out[cur] = (closes[cur] / pc - 1) * 100.0
    return out


# ── 거시 수집 (yfinance) ─────────────────────────────────────
def fetch_macro_histories(years: float = 3.0) -> dict:
    """yfinance: {sym: {date(YYYY-MM-DD): 종가/값}} — 일별 종가(종가/금리).

    네트워크 실패한 심볼은 누락. (동기 호출 — 러너에서 to_thread로 격리)
    """
    import yfinance as yf
    period = f"{max(1, int(years))}y"
    out = {}
    for sym in (SYM_NQ, SYM_USDKRW, SYM_US10Y, SYM_KOSPI):
        try:
            hist = yf.Ticker(sym).history(period=period, interval='1d', auto_adjust=True)
            if hist is None or hist.empty:
                logger.warning(f"[macro] yfinance 빈응답 {sym}")
                continue
            out[sym] = {idx.strftime('%Y-%m-%d'): float(row['Close'])
                        for idx, row in hist.iterrows()}
        except Exception:
            logger.exception(f"[macro] yfinance 실패 {sym}")
    return out


def overnight_session_return(us_closes: dict, kr_date: str) -> float:
    """한국 T일과 짝지을 직전 미국 세션 등락(%).

    미국 세션(D일, 미국날짜)은 KST 다음날 아침에 끝남 → 한국 T일 전야 = 미국 T-1행.
    T-1에 세션이 없으면(주말) 더 과거 최근 세션을 사용.
    """
    d = datetime.strptime(kr_date, '%Y%m%d')
    for back in range(1, 7):
        cand = (d - timedelta(days=back)).strftime('%Y-%m-%d')
        prev = (datetime.strptime(cand, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        if prev in us_closes and cand in us_closes:
            return (us_closes[cand] / us_closes[prev] - 1) * 100.0
    return None


# ── 저장 / 조회 ──────────────────────────────────────────────
def upsert_daily_record(record: dict, path: str = None):
    """하루 1줄 원칙 — 같은 날짜는 병합(기존값 보존, null 아닌 신규값만 덮어씀), 없으면 append.

    병합 이유: daily 실패분을 backfill로 소급 채울 때, 기존 행의 regime/short_term/corr
    같은 후속 필드가 지워지지 않아야 함 (2026-09-13 9/12 실패분 복구에서 발견).
    """
    path = path or JSONL_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            rows = [json.loads(l) for l in f if l.strip()]
    merged = None
    for i, r in enumerate(rows):
        if r.get('date') == record['date']:
            merged = dict(r)
            for k, v in record.items():
                if v is not None:
                    merged[k] = v
            rows[i] = merged
            break
    if merged is None:
        rows.append(record)
    rows.sort(key=lambda r: r['date'])
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(tmp, path)


def load_history(path: str = None) -> list:
    path = path or JSONL_PATH
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def pearson(xs: list, ys: list):
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) ** 0.5


def rolling_corr(history: list, key_a: str, key_b: str, window: int = CORR_WINDOW):
    """최근 window개 행에서 key_a/key_b 상관. 결측 있는 행은 양쪽 모두 제외."""
    pairs = [(r.get(key_a), r.get(key_b)) for r in history[-(window + 10):]]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    return pearson([p[0] for p in pairs[-window:]], [p[1] for p in pairs[-window:]])


# ── 단기 흐름 판정 (표시 전용 — 2026-09-08 Lee 요청) ─────────
# 3~7일 규모 단기 추세 라벨링. 방향=5거래일 누적(z 정규화), 지속=5일선/20일선 배열 일수.
SHORT_WIN = 5          # 누적 등락 윈도 (거래일)
SHORT_Z_TH = 0.5       # ±0.5σ 넘으면 흐름 있는 것으로 판정
SHORT_SIGMA_LOOKBACK = 60


def compute_short_term(kospi_closes: dict):
    """단기 흐름 — 방향(5일 누적, 변동성 z) + 지속(5일선>20일선 배열 연속일수).

    Returns: {'dir': '상승|중립|하락', 'ret5', 'z5', 'ma_state': bool,
              'ma_days': int(배열 지속일수), 'ma_dir': str} or None.
    """
    try:
        import pandas as pd
    except ImportError:
        return None
    s = pd.Series(kospi_closes).sort_index()
    if len(s) < SHORT_SIGMA_LOOKBACK + SHORT_WIN:
        return None
    close = float(s.iloc[-1])
    base = float(s.iloc[-(SHORT_WIN + 1)])
    if base <= 0:
        return None
    ret5 = (close / base - 1) * 100
    r5 = s.pct_change(SHORT_WIN) * 100
    sigma = float(r5.iloc[-SHORT_SIGMA_LOOKBACK:].std())
    # 변동성이 비정상적으로 작으면(합성 데이터 등) z 계산 불가 → 중립 처리
    z5 = (ret5 / sigma) if sigma and sigma >= 0.05 else None

    if z5 is None:
        dirn = '중립'
    elif z5 >= SHORT_Z_TH:
        dirn = '상승'
    elif z5 <= -SHORT_Z_TH:
        dirn = '하락'
    else:
        dirn = '중립'

    ma5s = s.rolling(SHORT_WIN).mean()
    ma20s = s.rolling(20).mean()
    state = (ma5s > ma20s).dropna()
    if state.empty:
        return None
    cur = bool(state.iloc[-1])
    ma_days = 0
    for v in state.iloc[::-1]:
        if bool(v) == cur:
            ma_days += 1
        else:
            break
    return {'dir': dirn, 'ret5': round(ret5, 2),
            'z5': round(z5, 2) if z5 is not None else None,
            'ma_state': cur, 'ma_days': ma_days}


# ── 국면 판정 (표시 전용 — 2026-09-08 Lee 승인 a안) ──────────
# 3게이지 다수결. 임계값 전부 관행값(a priori) — 최적화 금지, 매매 근거 아님.
# (근거: DCA 강도조절 백테스트 기각 2026-09-07 — 국면 라벨은 상황 인지 용도로만.)
REGIME_TREND_TH = -5.0     # 200일선 이격도: ≥0 ↑ / -5~0 → / <-5 ↓
REGIME_DD_TH = (-10.0, -20.0)  # 52주 고점 대비: ≥-10 ↑ / -10~-20 → / ≤-20 ↓
REGIME_MOM_TH = 5.0        # 3개월 수익률: ≥+5 ↑ / ±5 → / ≤-5 ↓


def compute_regime(kospi_closes: dict):
    """3게이지 국면 판정 — 추세(200일선 이격)·낙폭(52주 고점 대비)·모멘텀(3개월).

    kospi_closes: {YYYY-MM-DD: close} (yfinance ^KS11, ~3년 — 200일선에 충분).
    Returns: {'label', 'trend', 'dd', 'mom', 'trend_dir', 'dd_dir', 'mom_dir'} or None.
    """
    try:
        import pandas as pd
    except ImportError:
        return None
    s = pd.Series(kospi_closes).sort_index()
    if len(s) < 250:
        return None
    close = float(s.iloc[-1])
    ma200 = float(s.rolling(200).mean().iloc[-1])
    if ma200 <= 0:
        return None
    trend = (close / ma200 - 1) * 100
    hi250 = float(s.iloc[-250:].max())
    dd = (close / hi250 - 1) * 100
    mom = (close / float(s.iloc[-63]) - 1) * 100 if float(s.iloc[-63]) > 0 else None

    t_dir = '↑' if trend >= 0 else ('→' if trend >= REGIME_TREND_TH else '↓')
    d_dir = '↑' if dd >= REGIME_DD_TH[0] else ('→' if dd >= REGIME_DD_TH[1] else '↓')
    if mom is None:
        m_dir = '→'
    else:
        m_dir = '↑' if mom >= REGIME_MOM_TH else ('→' if mom >= -REGIME_MOM_TH else '↓')
    ups = [t_dir, d_dir, m_dir].count('↑')
    downs = [t_dir, d_dir, m_dir].count('↓')
    if ups >= 2:
        label = '강세장'
    elif downs >= 2:
        label = '하락장'
    else:
        label = '보합·전환기'

    # 고점대비·3개월의 현재 상태 — 10거래일 전 값 대비 변화로 판정 (±3%p, a priori)
    #   반등 = 개선 / 바닥권 = 횡보 / 진행 = 악화
    dd_chg = mom_chg = None
    if len(s) >= 270:
        dd_10 = (float(s.iloc[-11]) / float(s.iloc[-260:-10].max()) - 1) * 100
        dd_chg = dd - dd_10
        base10 = float(s.iloc[-73])
        if base10 > 0 and mom is not None:
            mom_chg = mom - ((float(s.iloc[-11]) / base10 - 1) * 100)

    def _state(chg):
        if chg is None:
            return None
        if chg >= 3:
            return '반등'
        if chg <= -3:
            return '진행'
        return '바닥권'

    return {'label': label, 'trend': round(trend, 1), 'dd': round(dd, 1),
            'mom': round(mom, 1) if mom is not None else None,
            'trend_dir': t_dir, 'dd_dir': d_dir, 'mom_dir': m_dir,
            'dd_state': _state(dd_chg), 'dd_chg': round(dd_chg, 1) if dd_chg is not None else None,
            'mom_state': _state(mom_chg), 'mom_chg': round(mom_chg, 1) if mom_chg is not None else None}


# ── 파이프라인 ───────────────────────────────────────────────
async def run_daily(token: str, today_iso: str = None) -> dict:
    """당일 기록 생성 + 저장. today_iso=None이면 오늘."""
    today = (today_iso or date.today().strftime('%Y%m%d')).replace('-', '')
    today_disp = f"{today[4:6]}-{today[6:]}"

    # 1) 국내 일봉 (반도체 + 기타 ETF)
    kr = await fetch_kr_daily_closes(token, today, SEMIS + OTHER_ETFS)

    # 휴장 판정 — 대표 종목 최신 캔들이 오늘이 아니면 국내 휴장: 무음 스킵.
    # (주말은 스케줄 자체가 없고, 평일 휴장일엔 에러 경고를 보내지 않는다.
    #  그날 밤 미국 세션은 다음 거래일 행의 '전야'로 시차정렬에 자동 반영됨.)
    samsung = kr.get('005930', {})
    if samsung and max(samsung.keys()) < today:
        logger.info(f"[macro] {today} 국내 휴장 (최신 캔들 {max(samsung.keys())}) — 스킵")
        return {}

    # 2) 거시 (동기 → to_thread)
    macro = await asyncio.to_thread(fetch_macro_histories)

    # 3) 등락률 조립
    semis_ret = {c: _daily_returns(kr[c]) for c in kr if c in [s[0] for s in SEMIS]}
    others_ret = {c: _daily_returns(kr[c]) for c in kr if c in [s[0] for s in OTHER_ETFS]}

    def block_avg(rets: dict, d: str):
        vals = [r.get(d) for r in rets.values() if r.get(d) is not None]
        return (sum(vals) / len(vals)) if vals else None

    semis_today = block_avg(semis_ret, today)
    others_today = block_avg(others_ret, today)

    # 기타 섹터 개별 등락 (리포트 상위 표시용)
    others_detail = []
    for code, name in OTHER_ETFS:
        v = others_ret.get(code, {}).get(today)
        if v is not None:
            others_detail.append((name, v))
    others_detail.sort(key=lambda t: -t[1])

    # 4) 거시 (전야 세션 정렬)
    nq_r = overnight_session_return(macro.get(SYM_NQ, {}), today)
    usd_closes = macro.get(SYM_USDKRW, {})
    usd_prev = usd_r = None
    d = datetime.strptime(today, '%Y%m%d')
    for back in range(0, 7):
        cand = (d - timedelta(days=back)).strftime('%Y-%m-%d')
        prev = (d - timedelta(days=back + 1)).strftime('%Y-%m-%d')
        if cand in usd_closes and prev in usd_closes:
            usd_prev, usd_r = usd_closes[cand], (usd_closes[cand] / usd_closes[prev] - 1) * 100.0
            break
    us10y = None
    for back in range(0, 7):
        cand = (d - timedelta(days=back)).strftime('%Y-%m-%d')
        if cand in macro.get(SYM_US10Y, {}):
            us10y = macro[SYM_US10Y][cand]
            break
    kospi_r = None
    for back in range(0, 7):
        cand = (d - timedelta(days=back)).strftime('%Y-%m-%d')
        prev = (d - timedelta(days=back + 1)).strftime('%Y-%m-%d')
        if cand in macro.get(SYM_KOSPI, {}) and prev in macro.get(SYM_KOSPI, {}):
            kospi_r = (macro[SYM_KOSPI][cand] / macro[SYM_KOSPI][prev] - 1) * 100.0
            break

    # 국면 + 단기 흐름 판정 (표시 전용 — 같은 kospi 히스토리 재사용, 신규 호출 없음)
    regime = compute_regime(macro.get(SYM_KOSPI, {}))
    short_term = compute_short_term(macro.get(SYM_KOSPI, {}))

    # 시장 폭 확장 지표 (2026-09-15 지시서 — 표시 전용, MDC 일봉 패널 기반)
    breadth = {}
    try:
        import market_breadth as mb
        panel, ohlc = mb._load()
        breadth = mb.snapshot(panel, ohlc)
    except Exception:
        logger.exception('[macro] breadth 지표 계산 실패')

    # 수급(외인·기관·개인, 백만원→억원) — ka10066 당일 종목별 스냅샷에서 추출
    # 대상: 반도체 블록 3종 + 기타 13 섹터 ETF (총 16종목)
    foreign_net = None
    flows = {}
    try:
        from utils.rate_limiter import requests
        rows66, cont66, nk66 = [], 'N', ''
        for _page in range(40):
            r66 = await requests.post(
                config.get_host_url() + '/api/dostk/mrkcond',
                headers={'Content-Type': 'application/json;charset=UTF-8',
                         'authorization': f'Bearer {token}', 'cont-yn': cont66,
                         'next-key': nk66, 'api-id': 'ka10066'},
                json={'mrkt_tp': '001', 'amt_qty_tp': '1', 'trde_tp': '0',
                      'stex_tp': '1'})
            d66 = r66.json()
            rows66.extend(d66.get('opaf_invsr_trde') or [])
            cont66 = r66.headers.get('cont-yn', 'N')
            nk66 = r66.headers.get('next-key', '')
            if cont66 != 'Y':
                break
        want = {c for c, _ in SEMIS} | {c for c, _ in OTHER_ETFS}

        def _beok(key, it):
            s = str(it.get(key, '0')).replace('+', '')
            if s.startswith('--'):
                s = '-' + s[2:]
            try:
                return round(int(float(s)) / 100, 1)  # 백만원 → 억원
            except (ValueError, TypeError):
                return None

        for it in rows66:
            code = str(it.get('stk_cd', '')).strip()
            if code not in want:
                continue
            flows[code] = {'frgnr': _beok('frgnr_invsr', it),
                           'orgn': _beok('orgn', it),
                           'ind': _beok('ind_invsr', it)}
        # 시장 전체 외국인 순매수 합계 (억원)
        foreign_net = round(sum((f.get('frgnr') or 0) for f in flows.values()), 1)
    except Exception:
        logger.exception('[macro] ka10066 수급 수집 실패')

    record = {
        'date': today,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'kospi_ret': round(kospi_r, 2) if kospi_r is not None else None,
        'semis_ret': round(semis_today, 2) if semis_today is not None else None,
        'semis_detail': {name: round(r.get(today), 2) for code, name in SEMIS
                         if (r := semis_ret.get(code)) and r.get(today) is not None},
        'others_ret': round(others_today, 2) if others_today is not None else None,
        'others_detail': {n: round(v, 2) for n, v in others_detail},
        'rotation_spread': (round(semis_today - others_today, 2)
                            if semis_today is not None and others_today is not None else None),
        'nq_overnight': round(nq_r, 2) if nq_r is not None else None,
        'usdkrw': round(usd_prev, 2) if usd_prev is not None else None,
        'usdkrw_ret': round(usd_r, 2) if usd_r is not None else None,
        'us10y': round(us10y, 2) if us10y is not None else None,
        'regime': regime,
        'short_term': short_term,
        'breadth': breadth,
        'flows': flows,
        'foreign_net_eok': foreign_net,
    }

    # 외국인 수급 T+1 보완 — 어제 행의 foreign_net이 비어 있으면 ka10058로 재조회해서 채운다
    try:
        yesterday = (datetime.strptime(today, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
        y_row = next((r for r in load_history() if r['date'] == yesterday), None)
        if y_row is not None and y_row.get('foreign_net_eok') is None:
            from foreign_flow import fetch_foreign_market_net
            y_net = await fetch_foreign_market_net(token, yesterday)
            if y_net is not None:
                y_row['foreign_net_eok'] = y_net
                upsert_daily_record(y_row)
                logger.info(f"[macro] 전일({yesterday}) 외국인 수급 보완: {y_net:+,.0f}억")
    except Exception:
        logger.exception('[macro] 전일 외국인 보완 실패')

    # 5) 60일 상관 (히스토리 충분할 때만)
    upsert_daily_record(record)
    history = load_history()
    if len(history) >= CORR_WINDOW:
        record['corr'] = {
            'kospi_nq': rolling_corr(history, 'kospi_ret', 'nq_overnight'),
            'kospi_usd': rolling_corr(history, 'kospi_ret', 'usdkrw_ret'),
            'semis_others': rolling_corr(history, 'semis_ret', 'others_ret'),
            'rotation_kospi': rolling_corr(history, 'rotation_spread', 'kospi_ret'),
        }
        for k in record['corr']:
            if record['corr'][k] is not None:
                record['corr'][k] = round(record['corr'][k], 2)
        upsert_daily_record(record)  # corr 포함 재저장
    return record


# ── 리포트 ───────────────────────────────────────────────────
def format_report(record: dict) -> str:
    today = record['date']
    disp = f"{today[4:6]}-{today[6:]}"
    history = load_history()
    hist5 = history[-5:]  # 과거→오늘 5행 (오늘은 upsert 후 포함)

    def pct(v):
        return 'N/A' if v is None else f"{v:+.2f}%"

    def seq(key, fmt):
        """hist5의 key 값 시퀀스 (과거→오늘). 결측은 '-'."""
        return ' / '.join('-' if r.get(key) is None else fmt(r.get(key)) for r in hist5)

    f1 = lambda v: f"{v:+.1f}"
    lines = [f"📊 [거시 모니터 {disp}] KOSPI {pct(record.get('kospi_ret'))}"]

    reg = record.get('regime')
    if reg:
        mom_s = 'N/A' if reg.get('mom') is None else f"{reg['mom']:+.1f}%"
        # 라벨 지속일수 (regime 기록이 있는 행부터)
        dur = 0
        for r in reversed(history):
            if (r.get('regime') or {}).get('label') == reg.get('label'):
                dur += 1
            else:
                break
        dur_s = f" {dur}일째" if dur >= 1 else ''
        lines.append(
            f"🏷 국면: {reg['label']}{dur_s} "
            f"(200일선 {reg['trend']:+.1f}%{reg['trend_dir']} · "
            f"고점대비 {reg['dd']:+.1f}%{reg['dd_dir']} · "
            f"3개월 {mom_s}{reg['mom_dir']})"
        )
        if reg.get('dd_state'):
            ms = reg.get('mom_state')
            mom_chg_s = f" ({reg['mom_chg']:+.1f}%p)" if ms and reg.get('mom_chg') is not None else ''
            lines.append(f"   ↳ 고점대비 {reg['dd_state']} (10일 {reg['dd_chg']:+.1f}%p)"
                         + (f" · 3개월 {ms}{mom_chg_s}" if ms else ""))
        # 교차검증: 전종목 200일선 위 비율 + 200일선 기울기 (2026-09-15 지시서 4번)
        b = record.get('breadth') or {}
        if b.get('pct_above_ma200') is not None:
            slope_s = ''
            if b.get('ma200_slope_20d') is not None:
                slope_s = f" · 기울기 {b['ma200_slope_20d']:+.1f}%"
                if b.get('ma200_slope_20d_prev') is not None:
                    slope_s += f" (20일전 {b['ma200_slope_20d_prev']:+.1f}%)"
            lines.append(f"   ↳ 200일선 위 종목 {b['pct_above_ma200']:.0f}%{slope_s}")

    st = record.get('short_term')
    if st:
        z_s = f"{st['z5']:+.1f}σ" if st.get('z5') is not None else 'N/A'
        ma_s = ('5일선>20일선' if st.get('ma_state') else '5일선<20일선') + f" {st['ma_days']}일째"
        b = record.get('breadth') or {}
        adx_s = ''
        if b.get('adx_14') is not None:
            adx = b['adx_14']
            word = '추세 약함' if adx < 20 else ('약한 추세' if adx <= 25 else '추세 뚜렷')
            adx_s = f" · ADX {adx:.0f} ({word})"
        if st['dir'] == '중립':
            lines.append(f"⚡ 단기: 중립 (5일 {st['ret5']:+.1f}%·{z_s} / {ma_s}){adx_s}")
        else:
            band = '약한 ' if abs(st.get('z5') or 0) < 0.8 else ''
            lines.append(f"⚡ 단기: {band}{st['dir']} 흐름 (5일 {st['ret5']:+.1f}%·{z_s} / {ma_s}){adx_s}")

    semis_d = record.get('semis_detail') or {}
    parts = ' · '.join(f"{n} {pct(v)}" for n, v in semis_d.items())
    lines.append("━━ 오늘 등락 ━━ (5일 흐름: 과거→오늘)")
    lines.append(f"🔺 반도체 블록 {pct(record.get('semis_ret'))}  "
                 f"5일: {seq('semis_ret', f1)}" + (f"\n   ({parts})" if parts else ""))
    others_d = record.get('others_detail') or {}
    top = ' / '.join(f"{n} {pct(v)}" for n, v in list(others_d.items())[:4])
    lines.append(f"🔹 기타 섹터 {pct(record.get('others_ret'))}  5일: {seq('others_ret', f1)}")
    lines.append(f"   (13개 평균: {top} …)")
    lines.append(f"   KOSPI {pct(record.get('kospi_ret'))}  5일: {seq('kospi_ret', f1)}")

    # 수급 흐름 (ka10066 당일 스냅샷 — 외인+기관 순매수, 백만원→억원)
    flows_today = record.get('flows') or {}
    if flows_today:
        semi_codes = [c for c, _ in SEMIS]
        other_codes = [c for c, _ in OTHER_ETFS]

        def _net(fls, codes):
            t = 0.0
            for c in codes:
                f = fls.get(c) or {}
                t += (f.get('frgnr') or 0) + (f.get('orgn') or 0)
            return t

        def _net_days(codes, rows):
            """rows 중 flows 기록된 행의 외인+기관 순매수 합계 (억원)."""
            t = 0.0
            for r in rows:
                fl = r.get('flows') or {}
                for c in codes:
                    f = fl.get(c) or {}
                    t += (f.get('frgnr') or 0) + (f.get('orgn') or 0)
            return t

        semi_today = _net(flows_today, semi_codes)
        others_today = _net(flows_today, other_codes)
        # 5일 누적 (flows 기록된 행만 합산 — 당일 포함 최근 5행)
        semi5 = _net_days(semi_codes, history)
        others5 = _net_days(other_codes, history)
        lines.append(f"   💠 수급(외인+기관): 반도체블록 {semi_today:+,.0f}억 · "
                     f"기타 {others_today:+,.0f}억 | 5일 누적: 반도체 {semi5:+,.0f}억 / "
                     f"기타 {others5:+,.0f}억")
        per5 = sorted(((name, _net_days([c], history)) for c, name in OTHER_ETFS),
                      key=lambda t: -t[1])
        if per5 and (abs(per5[0][1]) >= 3 or abs(per5[-1][1]) >= 3):
            top_in, top_out = per5[0], per5[-1]
            if top_in[0] != top_out[0]:
                lines.append(f"   ↳ 두드러진 섹터: {top_in[0]} {top_in[1]:+,.0f}억 유입 · "
                             f"{top_out[0]} {top_out[1]:+,.0f}억 이탈")

    lines.append("━━ 회전 관찰 ━━")
    sp = record.get('rotation_spread')
    if sp is None:
        lines.append("🔄 스프레드 N/A (일부 데이터 결측)")
    else:
        if sp < 0:
            lines.append(f"🔄 스프레드 {sp:+.2f}%p — 반도체→기타 이탈")
        elif sp > 0:
            lines.append(f"🔄 스프레드 {sp:+.2f}%p — 반도체 집중")
        else:
            lines.append("🔄 스프레드 0.00%p — 균형")
        lines.append(f"   최근 5일: {seq('rotation_spread', f1)} (과거→오늘)")
    # 시장 폭: A/D 라인 (2026-09-15 지시서 3번 — 회전과 병렬 배치)
    b = record.get('breadth') or {}
    if b.get('ad_line') is not None:
        lines.append(f"   A/D: 상승 {b.get('advancers', '-')} / 하락 {b.get('decliners', '-')}"
                     f" · 라인 변화 {b.get('ad_change_1d', 0):+d} (누적 {b['ad_line']:+d})")
    # 외인 시장 수급: 당일 값 + z5/z20/z60 (ka10058 축적본, 2026-09-16 지시서 6번)
    fn = record.get('foreign_net_eok')
    if fn is not None:
        fvals = [r.get('foreign_net_eok') for r in history
                 if r.get('foreign_net_eok') is not None]

        def fz(n):
            vals = fvals[-n:]
            if len(vals) < max(10, n // 2):
                return None
            m = sum(vals) / len(vals)
            sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
            return (fn - m) / sd if sd > 0 else None

        def zf(v):
            return 'N/A' if v is None else f"{v:+.1f}σ"

        z5, z20, z60 = fz(5), fz(20), fz(60)
        trend_word = ''
        if z5 is not None and z60 is not None and z5 != 0 and z60 != 0:
            trend_word = ' · 추세 확정' if (z5 > 0) == (z60 > 0) else ' · 전환 구간'
        lines.append(f"   외인 시장 순매수 {fn:+,.0f}억 "
                     f"(z5 {zf(z5)} / z20 {zf(z20)} / z60 {zf(z60)}){trend_word}")

    lines.append("━━ 전야 미국 ━━ (5일 흐름: 과거→오늘)")
    lines.append(f"🌐 나스닥F {pct(record.get('nq_overnight'))}  5일: {seq('nq_overnight', f1)}")
    usd = record.get('usdkrw')
    usd_s = f"{usd:,.0f}" if usd is not None else 'N/A'
    lines.append(f"   달러 {usd_s}  5일: {seq('usdkrw', lambda v: f'{v:,.0f}')}")
    us10 = record.get('us10y')
    us10_s = f"{us10:.2f}%" if us10 is not None else 'N/A%'
    lines.append(f"   미10Y {us10_s}  5일: {seq('us10y', lambda v: f'{v:.2f}')}")
    b = record.get('breadth') or {}
    if b.get('atr14_pct') is not None:
        pctile = b.get('atr14_pctile')
        flag = ''
        if pctile is not None and pctile >= 80:
            flag = ' — 고변동'
        pctile_s = f" (역내 {pctile:.0f}%ile)" if pctile is not None else ''
        lines.append(f"   변동성(ATR14) {b['atr14_pct']:.1f}%{pctile_s}{flag}")

    corr = record.get('corr') or (history[-1].get('corr') if history else None)
    # 비교 기준값: 20거래일 전 corr, 없으면 corr이 있는 가장 오래된 행 (기록 시작값)
    base_corr, base_date = None, None
    if corr:
        cand = history[-21] if len(history) >= 21 else None
        if cand and cand.get('corr'):
            base_corr, base_date = cand['corr'], cand['date']
        else:
            for r in history:
                if r.get('corr'):
                    base_corr, base_date = r['corr'], r['date']
                    break
    base_s = f", 괄호={base_date[4:6]}.{base_date[6:]} 기준 대비" if base_date else ''
    lines.append(f"━━ 상관 (60일{base_s}) ━━")
    if corr:
        def c(key):
            now = corr.get(key)
            if now is None:
                return 'N/A'
            if base_corr and base_corr.get(key) is not None:
                old = base_corr[key]
                d = abs(now) - abs(old)
                word = '강화' if d >= 0.05 else ('약화' if d <= -0.05 else '유지')
                return f"{now:+.2f} (기준 {old:+.2f} → {word})"
            return f"{now:+.2f}"
            return f"{now:+.2f}"

        lines.append(f"   KOSPI↔나스닥F {c('kospi_nq')} | KOSPI↔달러 {c('kospi_usd')}")
        lines.append(f"   반도체↔기타 {c('semis_others')} | 회전↔KOSPI {c('rotation_kospi')}")
    else:
        lines.append(f"   기록 {len(history)}일 — 60일({CORR_WINDOW - len(history)}일 후)부터 표시")
    return '\n'.join(lines)
