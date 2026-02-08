"""
상태 저장 (SQLite)
이전 체크 결과를 기억하여 연속 에러 판단 및 복구 감지
"""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from checker import CheckResult

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


@dataclass
class StateChange:
    """상태 변화 판단 결과"""
    service_name: str
    current_ok: bool
    message: str
    error_count: int
    should_alert: bool       # 경고 알림 보내야 하는지
    is_recovery: bool        # 복구된 건지
    already_alerted: bool    # 이미 경고 알림 보낸 건지


class StateStore:
    def __init__(self, db_path: str = "monitor.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS service_state (
                service_name TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'ok',
                message TEXT DEFAULT '',
                error_count INTEGER DEFAULT 0,
                alerted INTEGER DEFAULT 0,
                last_checked TEXT,
                first_error_at TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS check_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT NOT NULL,
                status TEXT NOT NULL,
                checked_at TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def evaluate(self, result: CheckResult, threshold: int) -> StateChange:
        """
        현재 체크 결과를 이전 상태와 비교하여 알림 여부 판단

        Args:
            result: 체크 결과
            threshold: 연속 에러 몇 회부터 경고 (기본 2)
        """
        row = self.conn.execute(
            "SELECT status, error_count, alerted, first_error_at FROM service_state WHERE service_name = ?",
            (result.service_name,),
        ).fetchone()

        now_str = result.checked_at.isoformat()

        if row is None:
            prev_status, prev_error_count, prev_alerted, first_error_at = "ok", 0, 0, None
        else:
            prev_status, prev_error_count, prev_alerted, first_error_at = row

        if result.ok:
            # 정상
            is_recovery = prev_status != "ok" and prev_error_count >= threshold
            self.conn.execute("""
                INSERT INTO service_state (service_name, status, message, error_count, alerted, last_checked, first_error_at)
                VALUES (?, 'ok', ?, 0, 0, ?, NULL)
                ON CONFLICT(service_name) DO UPDATE SET
                    status='ok', message=?, error_count=0, alerted=0, last_checked=?, first_error_at=NULL
            """, (result.service_name, result.message, now_str, result.message, now_str))
            self._record_history(result.service_name, "ok", now_str)
            self.conn.commit()

            return StateChange(
                service_name=result.service_name,
                current_ok=True,
                message=result.message,
                error_count=0,
                should_alert=False,
                is_recovery=is_recovery,
                already_alerted=False,
            )
        else:
            # 에러
            new_error_count = prev_error_count + 1
            new_first_error = first_error_at or now_str
            should_alert = new_error_count >= threshold and prev_alerted == 0
            already_alerted = prev_alerted == 1
            new_alerted = 1 if (should_alert or already_alerted) else 0

            self.conn.execute("""
                INSERT INTO service_state (service_name, status, message, error_count, alerted, last_checked, first_error_at)
                VALUES (?, 'error', ?, ?, ?, ?, ?)
                ON CONFLICT(service_name) DO UPDATE SET
                    status='error', message=?, error_count=?, alerted=?, last_checked=?, first_error_at=?
            """, (
                result.service_name, result.message, new_error_count, new_alerted, now_str, new_first_error,
                result.message, new_error_count, new_alerted, now_str, new_first_error,
            ))
            self._record_history(result.service_name, "error", now_str)
            self.conn.commit()

            return StateChange(
                service_name=result.service_name,
                current_ok=False,
                message=result.message,
                error_count=new_error_count,
                should_alert=should_alert,
                is_recovery=False,
                already_alerted=already_alerted,
            )

    def _record_history(self, service_name: str, status: str, checked_at: str):
        """체크 히스토리 기록 (서비스당 최근 10개만 유지)"""
        self.conn.execute(
            "INSERT INTO check_history (service_name, status, checked_at) VALUES (?, ?, ?)",
            (service_name, status, checked_at),
        )
        # 오래된 기록 정리 (최근 10개만 유지)
        self.conn.execute("""
            DELETE FROM check_history WHERE id NOT IN (
                SELECT id FROM check_history WHERE service_name = ?
                ORDER BY id DESC LIMIT 10
            ) AND service_name = ?
        """, (service_name, service_name))

    def get_all_states(self) -> list[dict]:
        """모든 서비스 상태 조회 (디스플레이용, 히스토리 포함)"""
        rows = self.conn.execute(
            "SELECT service_name, status, message, error_count, last_checked FROM service_state ORDER BY service_name"
        ).fetchall()
        states = []
        for r in rows:
            history = self.conn.execute(
                "SELECT status FROM check_history WHERE service_name = ? ORDER BY id DESC LIMIT 10",
                (r[0],),
            ).fetchall()
            states.append({
                "name": r[0],
                "status": r[1],
                "message": r[2],
                "error_count": r[3],
                "last_checked": r[4],
                "history": [h[0] for h in reversed(history)],  # 오래된 순
            })
        return states

    def close(self):
        self.conn.close()
