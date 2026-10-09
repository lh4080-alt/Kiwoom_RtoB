# -*- coding: utf-8 -*-
"""일일 브리프 러너 — 스케줄러가 하루 2회(05:35·06:35 KST) 호출, 멱등.

발송 규칙 (Lee 확정 2026-10-09): 매 미국 세션 마감(16:00 ET, zoneinfo) + 30분 이후 1회.
  · 아직 시각 전이거나 그 세션을 이미 처리했으면 아무것도 하지 않음
  · 실행일 전 남은 미국 세션이 있으면 '예비', 없으면 '확정' (연휴·토요일 동일 로직)
처리: 수집 → V4 무결성 검사 → 렌더 → 기록(briefs.jsonl) → (BRIEF_SEND면) 텔레그램
      예상 갭 예측 기록 + 지난 예측 실측 채움, 확정 브리프에서 삼전 당김 발생 시 주기 소진 기록

  python brief_once.py            # 정기 (시각·중복 판정)
  python brief_once.py --force    # 판정 무시하고 생성 (발송은 BRIEF_SEND 따름)
  python brief_once.py --dry      # 생성·출력만 (기록·발송 없음)
"""
import asyncio
import json
import os
import sys
from datetime import datetime

AUTO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AUTO)
import brief_config as cfg  # noqa: E402

LOG = os.path.join(AUTO, '..', 'config', 'data', 'brief', 'briefs.jsonl')


async def main() -> int:
    from brief.build import KST, collect, render, session_frame
    from brief.dca import cycle_of, load_state, pull_status, save_state
    from brief.expected_gap import fill_actuals, load_kr_ohlc, log_prediction
    from brief.integrity import run_checks

    force, dry = '--force' in sys.argv, '--dry' in sys.argv
    now = datetime.now(KST)
    sf = session_frame(now)
    state = load_state()
    if not (force or dry):
        if now < sf['send_at']:
            print(f'[brief] 대기 — US {sf["d_last"]} 마감+30분 {sf["send_at"]:%m/%d %H:%M} 전')
            return 0
        if state.get('last_session') == str(sf['d_last']):
            print(f'[brief] 처리 완료된 세션 {sf["d_last"]} — 스킵')
            return 0

    d = await collect(now)
    fails = run_checks(d)
    text = render(d, state, fails)
    print(text)
    if dry:
        return 0

    rec = {'generated_at': now.isoformat(timespec='seconds'), 'us_session': str(sf['d_last']),
           'exec': str(sf['exec']), 'final': sf['final'], 'fails': fails, 'text': text,
           'sent': bool(cfg.BRIEF_SEND),
           'metrics': {
               'regime': d['regime']['label'], 'dd': d['regime']['dd'], 'mom': d['regime']['mom'],
               'pct_above200': d['breadth']['pct_above200'], 'ad_cum': d['breadth']['ad_cum'],
               'atr_pct': d['atr']['pct'], 'atr_pctile': d['atr']['pctile'],
               'us10y': d['us10y']['last'], 'usdkrw': d['usdkrw']['last'],
               'foreign_z20': d['foreign'].get('z20'), 'foreign_z60': d['foreign'].get('z60'),
               **{f'{c}_{k}': d['semi'][c][k] for c in d['semi']
                  for k in ('x', 'z', 'exp_gap', 'ma60', 'ma200', 'frgn20')}}}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')

    if cfg.BRIEF_SEND:
        from telegram.tel_send import tel_send
        await tel_send(text)

    # 예상 갭 사후 기록 + 회차 상태
    pred_like = {'exec': sf['exec'], 'sessions': d['sessions_used'],
                 'pending_sessions': [None] * (sf['total'] - len(sf['closed'])),
                 'by_code': {c: {'x': v['x'], 'beta': v['beta'], 'exp_gap': v['exp_gap']}
                             for c, v in d['semi'].items()}}
    log_prediction(pred_like)
    from modules.semi_trigger.token_provider import get_semi_token
    fill_actuals(await load_kr_ohlc(await get_semi_token()))
    state['last_session'] = str(sf['d_last'])
    # 당김은 확정 브리프에서 실제 당김이 발생한 경우에만 주기 소진으로 기록
    if sf['final'] and pull_status(sf['exec'], d['trigger'], state) == 'pull':
        state.setdefault('pulled_cycles', {})['005930'] = str(cycle_of(sf['exec']))
    save_state(state)
    print(f'[brief] 기록 완료 — US {sf["d_last"]} → KR {sf["exec"]} '
          f'({"확정" if sf["final"] else "예비"}) | 검증 실패 {len(fails)} | 발송 {cfg.BRIEF_SEND}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
