"""虚拟库 ID：固定前缀 + 12 位十进制自增补零。"""

from __future__ import annotations

import re
from typing import Iterable

# 与「UUID 前半段」等长的固定段（四组 8-4-4-4 + 末段 12 位序号）
VIRTUAL_LIBRARY_ID_PREFIX = "20260414-1144-6259-0824-"

_PATTERN = re.compile(re.escape(VIRTUAL_LIBRARY_ID_PREFIX) + r"(\d{12})$")


def next_virtual_library_id(existing_ids: Iterable[str]) -> str:
    seq = 1
    for sid in existing_ids:
        if not sid:
            continue
        m = _PATTERN.match(sid.strip())
        if m:
            seq = max(seq, int(m.group(1), 10) + 1)
    if seq > 10**12 - 1:
        raise ValueError("virtual library id sequence overflow (12 digits)")
    return f"{VIRTUAL_LIBRARY_ID_PREFIX}{seq:012d}"
