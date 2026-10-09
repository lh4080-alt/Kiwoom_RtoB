# -*- coding: utf-8 -*-
"""semi_trigger 설정 — brief_config.py의 재수출 (2026-10-09 단일 설정 원칙).

값은 automation/brief_config.py 한 곳에서만 정의한다. 이 파일은 기존 import 경로
(modules.semi_trigger.semi_config) 호환용이다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from brief_config import *  # noqa: E402,F401,F403
