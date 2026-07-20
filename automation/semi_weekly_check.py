# -*- coding: utf-8 -*-
"""주간 신호 건강검진 — 2단계 눌림 매수 신호의 롤링 성과 자동 재검증 (2026-07-16 Lee 확정).

룰 (판정은 반도체 축만, fx/nq 미개입):
  🛒🛒 강한 눌림: us_memory z ≤ -1.0  +  종목 60일선 위
  🛒 일반 눌림:  주식축 합성 z (us_mem 0.6 + legacy 0.4) ≤ -1.0  +  60일선 위
  알림 전체 = 강한 ∪ 일반. 진입 익영업일 시가, 5일 보유 기준.

매주 토 10:00 KST 작업스케줄러 실행:
  1. yfinance 5년 히스토리 재계산 (walk-forward z, 미래 누출 없음)
  2. C:\\market_data parquet 일봉 → 이벤트 forward return
  3. 6M/12M/5Y 윈도우 성과 vs 기저 → 판정 (알림 전체 기준)
  4. 4주 연속 약화 → 중지 권고 (히스테리시스, config/data/semi_weekly_state.json)
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BARS = Path(r"C:\market_data\bars_1d\stocks")
STATE_PATH = Path(__file__).resolve().parent.parent / "config" / "data" / "semi_weekly_state.json"

BASELINE = 20
DIP_THRESHOLD = -1.0
MA_DAYS = 60
HOLD = 5
WEAK_STREAK_STOP = 4
MIN_EVENTS = 5
TARGETS = {"005930": "삼성전자", "000660": "SK하이닉스"}


def build_signals() -> pd.DataFrame:
    """미국 반도체 축 z 히스토리 (US 주식 캘린더 — 결측 gap 없음)."""
    import yfinance as yf
    syms = ["MU", "WDC", "SNDK", "STX", "^SOX", "NVDA"]
    px = yf.download(syms, start="2020-10-01", auto_adjust=True, progress=False)["Close"]
    ret = px.pct_change(fill_method=None) * 100
    us_mem = ret[["MU", "WDC", "SNDK", "STX"]].mean(axis=1, skipna=True)
    legacy = ret[["^SOX", "NVDA"]].mean(axis=1, skipna=True)

    def wz(s):
        mean = s.shift(1).rolling(BASELINE, min_periods=BASELINE).mean()
        std = s.shift(1).rolling(BASELINE, min_periods=BASELINE).std()
        return ((s - mean) / std).where(std > 0)

    z_us, z_leg = wz(us_mem), wz(legacy)
    stock_z = 0.6 * z_us + 0.4 * z_leg
    stock_z = stock_z.where(z_us.notna() & z_leg.notna(),
                            z_us.where(z_leg.isna(), z_leg))
    sig = pd.DataFrame({"z_us": z_us, "stock_z": stock_z}).dropna(how="all")
    return sig[sig.index >= "2021-01-01"]


def load_bars(code: str) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for p in sorted((BARS / code).glob("*.parquet"))]
    df = (pd.concat(frames, ignore_index=True)
          .drop_duplicates(subset="dt").sort_values("dt").reset_index(drop=True))
    df["dt"] = pd.to_datetime(df["dt"])
    df["ma60"] = df["close"].rolling(MA_DAYS).mean()
    return df


def build_events(code: str, sig: pd.DataFrame) -> pd.DataFrame:
    bars = load_bars(code)
    dts = bars["dt"].values
    rows = []
    for d, r in sig.iterrows():
        i = np.searchsorted(dts, np.datetime64(d), side="right")
        if i < 1 or i >= len(bars):
            continue
        entry = bars["open"].iloc[i]
        prev = bars.iloc[i - 1]
        if not entry or entry <= 0 or pd.isna(prev["ma60"]):
            continue
        if i + HOLD - 1 >= len(bars):
            continue  # fwd 미확정 — 다음 검진에 반영
        fwd = (bars["close"].iloc[i + HOLD - 1] / entry - 1) * 100
        up = bool(prev["close"] > prev["ma60"])
        strong = bool(pd.notna(r["z_us"]) and r["z_us"] <= DIP_THRESHOLD and up)
        normal = bool(pd.notna(r["stock_z"]) and r["stock_z"] <= DIP_THRESHOLD and up)
        rows.append({"d": d, "fwd": fwd, "strong": strong, "alert": strong or normal})
    return pd.DataFrame(rows).set_index("d")


def window_stats(ev: pd.DataFrame, days_back) -> dict:
    g = ev if days_back is None else ev[ev.index >= (pd.Timestamp.now() - timedelta(days=days_back))]
    alert = g[g["alert"]]
    strong = g[g["strong"]]
    return {
        "base_mean": g["fwd"].mean() if len(g) else None,
        "n": int(len(alert)),
        "alert_mean": alert["fwd"].mean() if len(alert) else None,
        "alert_win": (alert["fwd"] > 0).mean() * 100 if len(alert) else None,
        "n_strong": int(len(strong)),
        "strong_mean": strong["fwd"].mean() if len(strong) else None,
    }


def fmt_window(label: str, s: dict) -> str:
    if s["base_mean"] is None:
        return f"  {label}: 데이터 없음"
    if s["n"] == 0:
        return f"  {label}: 알림 없음 (기저 {s['base_mean']:+.2f}%)"
    out = (f"  {label}: 전체 {s['alert_mean']:+.2f}% (승{s['alert_win']:.0f}%, n={s['n']})")
    if s["n_strong"]:
        out += f" · 강한 {s['strong_mean']:+.2f}%(n={s['n_strong']})"
    out += f" vs 기저 {s['base_mean']:+.2f}%"
    return out


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> int:
    sig = build_signals()
    state = load_state()
    lines = [
        f"🩺 [semi 신호 건강검진 {date.today().isoformat()}]",
        f"룰: 🛒🛒 us_mem z≤{DIP_THRESHOLD} / 🛒 주식축 z≤{DIP_THRESHOLD} (+60일선 위)",
        f"→ 익영업일 시가 매수, {HOLD}일 보유",
        "",
    ]
    for code, name in TARGETS.items():
        ev = build_events(code, sig)
        s6, s12, s5y = window_stats(ev, 183), window_stats(ev, 365), window_stats(ev, None)

        streak = int(state.get(code, {}).get("weak_streak", 0))
        if s6["n"] < MIN_EVENTS:
            verdict = f"⏸️ 판정 보류 (6M 알림 {s6['n']}건 < {MIN_EVENTS})"
        elif s6["alert_mean"] is not None and s6["base_mean"] is not None and s6["alert_mean"] > s6["base_mean"]:
            streak = 0
            verdict = "✅ 정상 (6M 알림 성과가 기저 상회)"
        else:
            streak += 1
            if streak >= WEAK_STREAK_STOP:
                verdict = f"🛑 중지 권고 ({streak}주 연속 약화 — 신호 사용 재검토)"
            else:
                verdict = f"⚠️ 약화 관찰 ({streak}주 연속, {WEAK_STREAK_STOP}주 도달 시 중지 권고)"
        state[code] = {"weak_streak": streak, "last_check": date.today().isoformat()}

        lines.extend([
            f"── [{code}] {name} ──",
            fmt_window("6M ", s6),
            fmt_window("12M", s12),
            fmt_window("5Y ", s5y),
            f"  판정: {verdict}",
            "",
        ])
    lines.append("※ fwd5d 미확정(최근 1주) 이벤트는 다음 검진에 반영")

    save_state(state)
    from telegram.tel_send import tel_send
    try:
        await tel_send("\n".join(lines))
    finally:
        try:
            from utils.rate_limiter import requests
            await requests.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
