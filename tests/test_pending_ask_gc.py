"""Garbage collection for the pending-ask file store (tools/_salt_common.py).

A Dify tool plugin has no scheduler of its own, so this runs opportunistically
inside save_pending_ask -- these tests exercise gc_stale_pending_asks directly
(pure filesystem, no fake Salt client needed) plus the save_pending_ask
integration point.
"""
from __future__ import annotations

import json
import os
import time

from tools import _salt_common


def _write_ask_file(ask_id: str, age_seconds: float) -> None:
    path = _salt_common._ask_path(ask_id)
    path.write_text(json.dumps({"card_id": ask_id}))
    stale_time = time.time() - age_seconds
    os.utime(path, (stale_time, stale_time))


def test_gc_removes_files_older_than_the_default_one_day_cutoff():
    _write_ask_file("stale-1", age_seconds=_salt_common.STALE_ASK_MAX_AGE_SECONDS + 60)
    _write_ask_file("stale-2", age_seconds=_salt_common.STALE_ASK_MAX_AGE_SECONDS * 3)

    removed = _salt_common.gc_stale_pending_asks()

    assert removed == 2
    assert _salt_common.load_pending_ask("stale-1") is None
    assert _salt_common.load_pending_ask("stale-2") is None


def test_gc_leaves_recent_files_alone():
    _write_ask_file("fresh-1", age_seconds=60)
    _write_ask_file("borderline", age_seconds=_salt_common.STALE_ASK_MAX_AGE_SECONDS - 60)

    removed = _salt_common.gc_stale_pending_asks()

    assert removed == 0
    assert _salt_common.load_pending_ask("fresh-1") is not None
    assert _salt_common.load_pending_ask("borderline") is not None


def test_gc_honors_a_custom_max_age():
    _write_ask_file("ten-minutes-old", age_seconds=600)

    removed = _salt_common.gc_stale_pending_asks(max_age_seconds=300)

    assert removed == 1
    assert _salt_common.load_pending_ask("ten-minutes-old") is None


def test_gc_ignores_stray_non_json_files_in_the_same_directory():
    stray = _salt_common._state_dir() / "some.tmp"
    stray.write_text("leftover from a crashed write")
    old_time = time.time() - (_salt_common.STALE_ASK_MAX_AGE_SECONDS * 2)
    os.utime(stray, (old_time, old_time))

    removed = _salt_common.gc_stale_pending_asks()

    assert removed == 0
    assert stray.exists()


def test_save_pending_ask_sweeps_stale_siblings_on_every_write():
    _write_ask_file("old-abandoned-ask", age_seconds=_salt_common.STALE_ASK_MAX_AGE_SECONDS + 3600)

    _salt_common.save_pending_ask({"card_id": "brand-new-ask"})

    assert _salt_common.load_pending_ask("old-abandoned-ask") is None
    assert _salt_common.load_pending_ask("brand-new-ask") == {"card_id": "brand-new-ask"}
