"""
알림 시스템
콘솔 로그 전용
"""

import logging
from datetime import datetime, timezone, timedelta

from state import StateChange

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class Alerter:
    def __init__(self):
        pass

    def process(self, change: StateChange):
        """상태 변화에 따라 알림 처리"""
        if change.is_recovery:
            self._log_recovery(change)
        elif change.should_alert:
            self._log_alert(change)
        elif not change.current_ok and change.error_count == 1:
            logger.info(f"[대기] {change.service_name} - 에러 1회 (임계값 미달)")

    def _log_alert(self, change: StateChange):
        """경고 로그"""
        now = datetime.now(KST).strftime("%m/%d %H:%M")
        logger.warning(
            f"[경고] {change.service_name} | {change.message} | "
            f"연속 {change.error_count}회 | {now}"
        )

    def _log_recovery(self, change: StateChange):
        """복구 로그"""
        now = datetime.now(KST).strftime("%m/%d %H:%M")
        logger.info(f"[복구] {change.service_name} | 정상 복구됨 | {now}")
