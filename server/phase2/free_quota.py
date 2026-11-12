"""Daily first-come-first-served free quota (resets at local midnight).

One row per calendar day (server TZ, Asia/Shanghai). There is no per-user
tracking on purpose: the campaign is a shared daily budget, whoever orders
first gets a free slot that day. `consume()` must run inside the caller's
transaction so the counter moves together with the order record.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

TABLE = 'phase2_free_daily'
SETTINGS = 'phase2_free_settings'


def configured_total():
    raw = (os.getenv('PHASE2_FREE_QUOTA_TOTAL') or '').strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise ValueError('PHASE2_FREE_QUOTA_TOTAL 必须为整数') from None
    return max(0, value)


def enabled():
    try:
        return configured_total() > 0
    except ValueError:
        return False


def today():
    """Local calendar day; the server runs with TZ=Asia/Shanghai."""
    return time.strftime('%Y-%m-%d')


def _total_or_zero():
    try:
        return configured_total()
    except ValueError:
        logger.warning('PHASE2_FREE_QUOTA_TOTAL is not an integer; free quota disabled')
        return 0


def _settings_total(db):
    row = db.execute('SELECT total FROM ' + SETTINGS + ' WHERE id=1').fetchone()
    return max(0, int(row[0])) if row else None


def status(store):
    env_total = _total_or_zero()
    day = today()
    with store.connection() as db:
        total = _settings_total(db)
        if total is None:
            total = env_total
        if total <= 0:
            return {'enabled': False, 'total': 0, 'used': 0, 'remaining': 0, 'day': day}
        row = db.execute('SELECT total,used FROM ' + TABLE + ' WHERE day=?', (day,)).fetchone()
    stored_total = max(int(row[0]), total) if row else total
    used = int(row[1]) if row else 0
    return {'enabled': stored_total > 0, 'total': stored_total, 'used': used,
            'remaining': max(0, stored_total - used), 'day': day}


def consume(store, db=None):
    """Atomically take one free slot for today. Returns True only for the winner."""
    now = time.time()
    day = today()
    if db is None:
        with store.transaction() as conn:
            return consume(store, conn)
    total = _settings_total(db)
    if total is None:
        total = _total_or_zero()
    if total <= 0:
        return False
    db.execute('INSERT OR IGNORE INTO ' + TABLE + '(day,total,used,updated_at) VALUES(?,?,0,?)',
               (day, total, now))
    db.execute('UPDATE ' + TABLE + ' SET total=? WHERE day=? AND total<>?', (total, day, total))
    cursor = db.execute('UPDATE ' + TABLE + ' SET used=used+1, updated_at=? WHERE day=? AND used<?',
                        (now, day, total))
    return getattr(cursor, 'rowcount', 0) == 1


def reset(store, day=None):
    """Test helper: clear one day's counter."""
    with store.transaction() as db:
        db.execute('DELETE FROM ' + TABLE + ' WHERE day=?', (day or today(),))
