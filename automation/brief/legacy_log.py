# -*- coding: utf-8 -*-
"""병행 운영용 — 기존 알림(거시 모니터·semi_trigger) 문구 기록. 발송 여부와 무관하게 항상 기록."""
import json
import os
from datetime import datetime

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'config', 'data', 'brief', 'legacy_log.jsonl')


def log_legacy(kind: str, ref_date: str, text: str):
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'kind': kind, 'date': str(ref_date),
                                'logged_at': datetime.now().isoformat(timespec='seconds'),
                                'text': text}, ensure_ascii=False) + '\n')
    except Exception:
        pass   # 기록 실패가 기존 알림을 막으면 안 됨
