# -*- coding: utf-8 -*-
"""브리프 수집 결과(d) 직렬화 — 골든 픽스처용. date·datetime·numpy 값을 태그로 보존."""
import json
from datetime import date, datetime

import numpy as np


def _enc(o):
    if isinstance(o, datetime):
        return {'__dt__': o.isoformat()}
    if isinstance(o, date):
        return {'__date__': o.isoformat()}
    if isinstance(o, dict):
        return {str(k): _enc(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_enc(v) for v in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if hasattr(o, 'date') and hasattr(o, 'isoformat') and not isinstance(o, str):
        return {'__date__': o.date().isoformat()}
    return o


def _dec(o):
    if isinstance(o, dict):
        if '__date__' in o and len(o) == 1:
            return date.fromisoformat(o['__date__'])
        if '__dt__' in o and len(o) == 1:
            return datetime.fromisoformat(o['__dt__'])
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(v) for v in o]
    return o


def dumps(d) -> str:
    return json.dumps(_enc(d), ensure_ascii=False, indent=1)


def loads(s: str):
    return _dec(json.loads(s))
