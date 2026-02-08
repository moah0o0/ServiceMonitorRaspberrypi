"""
알림 시스템
텔레그램 + 콘솔 로그
"""

import logging
from datetime import datetime, timezone, timedelta

import requests

from config import Config
from state import StateChange

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class Alerter:
    def __init__(self, config: Config):
        self.config = config
        self.telegram_enabled = bool(config.telegram_bot_token and config.telegram_alert_channel)
        if not self.telegram_enabled:
            logger.warning("텔레그램 설정 없음 - 콘솔 알림만 사용합니다")

    def process(self, change: StateChange):
        """상태 변화에 따라 알림 처리"""
        if change.is_recovery:
            self._send_recovery(change)
        elif change.should_alert:
            self._send_alert(change)
        elif not change.current_ok and change.error_count < self.config.consecutive_error_threshold:
            logger.info(f"[대기] {change.service_name} - 에러 {change.error_count}회 (임계값 미달)")

    def _send_alert(self, change: StateChange):
        """경고 알림"""
        now = datetime.now(KST).strftime("%m/%d %H:%M")
        text = (
            f"[경고] {change.service_name}\n"
            f"내용: {change.message}\n"
            f"연속 에러: {change.error_count}회\n"
            f"시간: {now}"
        )
        logger.warning(text.replace("\n", " | "))
        self._send_telegram(text)

    def _send_recovery(self, change: StateChange):
        """복구 알림"""
        now = datetime.now(KST).strftime("%m/%d %H:%M")
        text = (
            f"[복구] {change.service_name}\n"
            f"상태: 정상 복구됨\n"
            f"시간: {now}"
        )
        logger.info(text.replace("\n", " | "))
        self._send_telegram(text)

    def _send_telegram(self, text: str):
        """텔레그램 메시지 전송"""
        if not self.telegram_enabled:
            return
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": self.config.telegram_alert_channel,
                    "text": text,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error(f"텔레그램 전송 실패: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            logger.error(f"텔레그램 전송 오류: {e}")
