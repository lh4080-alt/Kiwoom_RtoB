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
    """하루 1줄 원칙 — 같은 날짜 기존 줄 교체, 없으면 append. (재실행 안전)"""
    path = path or JSONL_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            rows = [json.loads(l) for l in f if l.strip()]
    rows = [r for r in rows if r.get('date') != record['date']]
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
    return {'label': label, 'trend': round(trend, 1), 'dd': round(dd, 1),
            'mom': round(mom, 1) if mom is not None else None,
            'trend_dir': t_dir, 'dd_dir': d_dir, 'mom_dir': m_dir}


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

    # 국면 판정 (표시 전용 — 같은 kospi 히스토리 재사용, 신규 호출 없음)
    regime = compute_regime(macro.get(SYM_KOSPI, {}))

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
    }

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
    def pct(v):
        return 'N/A' if v is None else f"{v:+.2f}%"
    lines = [f"📊 [거시 모니터 {disp}] KOSPI {pct(record.get('kospi_ret'))}"]

    reg = record.get('regime')
    if reg:
        mom_s = 'N/A' if reg.get('mom') is None else f"{reg['mom']:+.1f}%"
        lines.append(
            f"🏷 국면: {reg['label']} "
            f"(추세 {reg['trend']:+.1f}%{reg['trend_dir']} · "
            f"고점대비 {reg['dd']:+.1f}%{reg['dd_dir']} · "
            f"3개월 {mom_s}{reg['mom_dir']})"
        )

    semis_d = record.get('semis_detail') or {}
    parts = ' · '.join(f"{n} {pct(v)}" for n, v in semis_d.items())
    lines.append("━━ 오늘 등락 ━━")
    lines.append(f"🔺 반도체 블록    {pct(record.get('semis_ret'))}" + (f"  ({parts})" if parts else ""))
    others_d = record.get('others_detail') or {}
    top = ' / '.join(f"{n} {pct(v)}" for n, v in list(others_d.items())[:4])
    lines.append(f"🔹 기타 섹터      {pct(record.get('others_ret'))}  (13개 평균: {top} …)")

    lines.append("━━ 회전 관찰 ━━")
    sp = record.get('rotation_spread')
    if sp is None:
        lines.append("🔄 스프레드 N/A (일부 데이터 결측)")
    else:
        streak = _streak_desc(history=load_history(), today=today)
        if sp < 0:
            lines.append(f"🔄 스프레드 {sp:+.2f}%p — 반도체→기타 이탈{streak}")
        elif sp > 0:
            lines.append(f"🔄 스프레드 {sp:+.2f}%p — 반도체 집중{streak}")
        else:
            lines.append("🔄 스프레드 0.00%p — 균형")
        history = load_history()
        recent = [r.get('rotation_spread') for r in history[-6:-1]]
        recent = [v for v in recent if v is not None]
        if recent:
            lines.append("   최근 5일: " + ' / '.join(f"{v:+.1f}" for v in recent))

    lines.append("━━ 전야 미국 (장 전 확정) ━━")
    usd_s = f"{record.get('usdkrw'):,.0f}" if record.get('usdkrw') else 'N/A'
    lines.append(f"🌐 나스닥F {pct(record.get('nq_overnight'))} | "
                 f"달러 {usd_s} ({pct(record.get('usdkrw_ret'))}) | "
                 f"미10Y {record.get('us10y') if record.get('us10y') is not None else 'N/A'}%")

    corr = record.get('corr')
    if corr:
        lines.append("━━ 상관 (60일) ━━")
        def c(v):
            return 'N/A' if v is None else f"{v:+.2f}"
        lines.append(f"   KOSPI↔나스닥F {c(corr.get('kospi_nq'))} | "
                     f"KOSPI↔달러 {c(corr.get('kospi_usd'))} | "
                     f"반도체↔기타 {c(corr.get('semis_others'))} | "
                     f"회전↔KOSPI {c(corr.get('rotation_kospi'))}")
    else:
        lines.append("━━ 상관 (60일) ━━")
        hist_len = len(load_history())
        lines.append(f"   기록 {hist_len}일 — 60일({CORR_WINDOW - hist_len}일 후)부터 표시")
    return '\n'.join(lines)


def _streak_desc(history: list, today: str) -> str:
    """오늘 포함 스프레드 부호 연속 일수 → ' (N일 연속)' or ''. N>=2만 표시."""
    sps = [r.get('rotation_spread') for r in history if r.get('rotation_spread') is not None]
    if not sps:
        return ''
    sign = 1 if sps[-1] > 0 else (-1 if sps[-1] < 0 else 0)
    n = 0
    for v in reversed(sps):
        s = 1 if v > 0 else (-1 if v < 0 else 0)
        if s == sign:
            n += 1
        else:
            break
    if n < 2 or sign == 0:
        return ''
    return f" ({n}일 연속)"
