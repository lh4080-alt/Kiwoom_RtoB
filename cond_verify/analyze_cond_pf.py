"""
analyze_cond_pf.py — 조건검색식 cond0~3 forward-return PF 분석 (Phase 1 골격).

데이터:
  - ../candle_quality_daily/*.csv  (eval_date, code, seq_ids, score, today_close ...)
  - //beelink/market_data/bars_1d/stocks/{code}/{YYYY}.parquet  (OHLCV)

측정 (cond 순수 엣지 — 봇 실제 매수와 분리):
  익일(D+1) 시가 매수 → D+1/D+3/D+5 종가 매도 수익률.
  cond(seq)별 / 교집합 강도별 / 보유기간별 PF.

실행: python cond_verify/analyze_cond_pf.py   (BEELINK에서 — 데이터가 거기)
데이터 부족 시(forward 미도래) 해당 trade는 'pending'으로 집계 제외.
"""
from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── 경로 ───────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
BOT_ROOT = _HERE.parent.parent                      # cond_verify/ 의 부모 = 봇 루트
CANDLE_DIR = BOT_ROOT / "candle_quality_daily"
MARKET_DATA_ROOT = Path(os.environ.get("MARKET_DATA_ROOT", r"\\beelink\market_data"))
SPEC_PATH = _HERE.parent / "cond_spec.json"

HOLD_DAYS = [1, 3, 5]                                # 보유기간 비교


# ── cond_spec ──────────────────────────────────────────────
def load_spec() -> dict:
	try:
		with open(SPEC_PATH, encoding="utf-8") as f:
			return json.load(f)
	except Exception as e:
		print(f"[warn] cond_spec 로드 실패: {e}")
		return {"conds": {}}


def seq_label(spec: dict, seq: int) -> str:
	c = spec.get("conds", {}).get(str(seq))
	return f"{c['cond']}({c['hts_name']})" if c else f"seq{seq}"


# ── 수집 기록 로드 ─────────────────────────────────────────
def parse_seqs(raw) -> set:
	"""seq_ids 필드 → {int}. 단일/콤마/공백 혼용 대응."""
	if raw is None:
		return set()
	return {int(x) for x in re.findall(r"\d+", str(raw))}


def load_collections() -> list[dict]:
	"""candle_quality_daily/*.csv → [{date, code, conds:set, score}]. (date,code) 단위 통합."""
	if not CANDLE_DIR.exists():
		print(f"[warn] candle_quality_daily 없음: {CANDLE_DIR}")
		return []
	merged: dict = {}                                # (date, code) -> {conds:set, score}
	for csv_path in sorted(CANDLE_DIR.glob("*.csv")):
		try:
			df = pd.read_csv(csv_path, dtype=str)
		except Exception as e:
			print(f"[warn] {csv_path.name} 읽기 실패: {e}")
			continue
		if "code" not in df.columns or "eval_date" not in df.columns:
			continue
		for _, row in df.iterrows():
			code = str(row.get("code", "")).strip().zfill(6)
			date = str(row.get("eval_date", "")).strip()[:10]
			if not code or not date:
				continue
			seqs = parse_seqs(row.get("seq_ids"))
			try:
				score = float(row.get("score")) if pd.notna(row.get("score")) else None
			except (ValueError, TypeError):
				score = None
			key = (date, code)
			e = merged.setdefault(key, {"conds": set(), "score": score})
			e["conds"] |= seqs
			if score is not None:
				e["score"] = score
	return [{"date": d, "code": c, "conds": v["conds"], "score": v["score"]}
	        for (d, c), v in merged.items()]


# ── 일봉 forward return ────────────────────────────────────
_bars_cache: dict = {}


def _load_bars(code: str, year: int) -> pd.DataFrame | None:
	key = (code, year)
	if key in _bars_cache:
		return _bars_cache[key]
	p = MARKET_DATA_ROOT / "bars_1d" / "stocks" / code / f"{year}.parquet"
	df = None
	if p.exists():
		try:
			df = pd.read_parquet(p)
			df["d"] = pd.to_datetime(df["dt"]).dt.strftime("%Y-%m-%d")
			df = df.sort_values("d").reset_index(drop=True)
		except Exception:
			df = None
	_bars_cache[key] = df
	return df


def forward_returns(code: str, eval_date: str) -> dict:
	"""익일 시가 매수 → D+1/D+3/D+5 종가 수익률(%). 미도래/결측은 None."""
	y = int(eval_date[:4])
	frames = [f for f in (_load_bars(code, y), _load_bars(code, y + 1)) if f is not None]
	if not frames:
		return {h: None for h in HOLD_DAYS}
	bars = pd.concat(frames, ignore_index=True).drop_duplicates("d").sort_values("d").reset_index(drop=True)
	after = bars[bars["d"] > eval_date].reset_index(drop=True)   # 익일 이후
	out = {}
	if after.empty:
		return {h: None for h in HOLD_DAYS}
	entry_open = float(after.iloc[0]["open"])
	if entry_open <= 0:
		return {h: None for h in HOLD_DAYS}
	for h in HOLD_DAYS:
		idx = h - 1                                  # D+1 보유=익일 종가, D+3=익일+2 ...
		if idx < len(after):
			exit_close = float(after.iloc[idx]["close"])
			out[h] = (exit_close - entry_open) / entry_open * 100.0 if exit_close > 0 else None
		else:
			out[h] = None                            # 아직 미도래
	return out


# ── PF 계산 ────────────────────────────────────────────────
def calc_pf(returns: list[float]) -> dict:
	if not returns:
		return {"n": 0, "win_rate": 0, "avg_win": 0, "avg_loss": 0, "pf": None, "expectancy": 0}
	wins = [r for r in returns if r > 0]
	losses = [-r for r in returns if r < 0]          # 손실 절대값
	tw, tl = sum(wins), sum(losses)
	return {
		"n": len(returns),
		"win_rate": round(len(wins) / len(returns) * 100, 1),
		"avg_win": round(tw / len(wins), 2) if wins else 0,
		"avg_loss": round(tl / len(losses), 2) if losses else 0,
		"pf": round(tw / tl, 2) if tl > 0 else (float("inf") if tw > 0 else None),
		"expectancy": round((tw - tl) / len(returns), 2),
	}


def _fmt(pf: dict) -> str:
	pfv = pf["pf"]
	pfs = "inf" if pfv == float("inf") else ("-" if pfv is None else f"{pfv}")
	return (f"n={pf['n']:>3}  승률={pf['win_rate']:>5}%  "
	        f"평균익={pf['avg_win']:>6}%  평균손={pf['avg_loss']:>6}%  "
	        f"기대값={pf['expectancy']:>6}%  PF={pfs}")


# ── 메인 ───────────────────────────────────────────────────
def main():
	spec = load_spec()
	cols = load_collections()
	print(f"=== cond 검증 forward-return PF (생성 {datetime.now():%Y-%m-%d %H:%M}) ===")
	print(f"수집 기록(date,code): {len(cols)}건  |  candle_quality_daily: {CANDLE_DIR}")
	if not cols:
		print("데이터 없음 — 수집 누적 후 재실행.")
		return

	# 각 수집건 forward return 계산
	trades = []          # {date, code, conds, strength, ret:{h:..}}
	pending = 0
	for c in cols:
		fr = forward_returns(c["code"], c["date"])
		if all(v is None for v in fr.values()):
			pending += 1
			continue
		trades.append({**c, "strength": len(c["conds"]), "ret": fr})
	print(f"forward 계산 완료: {len(trades)}건  |  미도래/결측: {pending}건\n")

	# 1) cond(seq)별 PF — 보유기간별
	print("── cond별 PF (보유기간 1/3/5일) ──")
	for seq in sorted(spec.get("conds", {}).keys(), key=int):
		seq_i = int(seq)
		sub = [t for t in trades if seq_i in t["conds"]]
		print(f"[{seq_label(spec, seq_i)}]  표본 {len(sub)}건")
		for h in HOLD_DAYS:
			rs = [t["ret"][h] for t in sub if t["ret"].get(h) is not None]
			print(f"   {h}일: {_fmt(calc_pf(rs))}")

	# 2) 교집합 강도별 PF
	print("\n── 교집합 강도별 PF ──")
	for strength in (1, 2, 3, 4):
		sub = [t for t in trades if t["strength"] == strength]
		if not sub:
			continue
		print(f"strength={strength}  종목 {len(sub)}건")
		for h in HOLD_DAYS:
			rs = [t["ret"][h] for t in sub if t["ret"].get(h) is not None]
			print(f"   {h}일: {_fmt(calc_pf(rs))}")

	# 3) 전체 baseline
	print("\n── 전체(모든 수집) ──")
	for h in HOLD_DAYS:
		rs = [t["ret"][h] for t in trades if t["ret"].get(h) is not None]
		print(f"   {h}일: {_fmt(calc_pf(rs))}")

	print("\n※ 가설 H1~H5는 cond_spec.json 참조. 표본<5는 통계 무의미(H5).")


if __name__ == "__main__":
	main()
