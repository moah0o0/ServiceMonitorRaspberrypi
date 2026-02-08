#!/usr/bin/env python3
"""
서비스 모니터링 메인 루프
라즈베리파이에서 주기적으로 서비스 상태를 점검하고 알림 전송

- 서비스 체크: CHECK_INTERVAL(기본 5분) 간격
- 디스플레이 갱신: 30초 간격 (시계 + 버튼 반응)
"""

import logging
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

from config import load_config, SERVICES
from checker import PocketBaseAuth, check_service
from state import StateStore
from alerter import Alerter
from display import Display

KST = timezone(timedelta(hours=9))
DISPLAY_REFRESH = 30  # 디스플레이 갱신 주기 (초)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self):
        self.config = load_config()
        self.state = StateStore(self.config.db_path)
        self.alerter = Alerter()
        self.display = Display()
        self.auth = PocketBaseAuth(self.config.pb_monitor_email, self.config.pb_monitor_password)
        self.running = True
        self._force_check = threading.Event()

    def _check_network(self) -> bool:
        """네트워크 연결 상태 확인 (DNS 8.8.8.8:53 접속 시도)"""
        try:
            sock = socket.create_connection(("8.8.8.8", 53), timeout=5)
            sock.close()
            return True
        except OSError:
            return False

    def check_all(self):
        """모든 서비스 1회 체크"""
        # 네트워크 상태 확인
        if not self._check_network():
            logger.warning("네트워크 연결 끊김!")
            self.display.set_system_error("네트워크 연결 끊김")
            self._refresh_display()
            return

        # 네트워크 복구 시 시스템 에러 해제
        self.display.set_system_error(None)

        now = datetime.now(KST).strftime("%H:%M:%S")
        logger.info(f"===== 체크 시작 ({now}) - {len(SERVICES)}개 서비스 =====")

        ok_count = 0
        for service in SERVICES:
            result = check_service(service, self.auth, self.config)
            change = self.state.evaluate(result, self.config.consecutive_error_threshold)

            status_icon = "O" if result.ok else "X"
            logger.info(f"  [{status_icon}] {service.name} - {result.message}")

            self.alerter.process(change)

            if result.ok:
                ok_count += 1

        logger.info(f"===== 체크 완료: {ok_count}/{len(SERVICES)} 정상 =====\n")

        # 디스플레이 즉시 갱신
        self.display.mark_dirty()
        self._refresh_display()

    def _refresh_display(self):
        """디스플레이 갱신"""
        states = self.state.get_all_states()
        self.display.update(states)

    def _display_loop(self):
        """디스플레이 전용 스레드: 0.2초마다 버튼 체크, 1초마다 화면 갱신"""
        sub_tick = 0
        while self.running:
            # 버튼 체크 (0.2초마다)
            btn = self.display.check_buttons()
            if btn["page_change"]:
                self._refresh_display()

            sub_tick += 1
            # 1초마다 (5 × 0.2초) 화면 갱신 + 페이지 타이머
            if sub_tick >= 5:
                sub_tick = 0
                self.display.advance_tick()
                if self.display.enabled:
                    self._refresh_display()

            time.sleep(0.2)

    def run(self):
        """메인 루프"""
        logger.info(f"모니터링 시작 (체크 간격: {self.config.check_interval}초, 화면 갱신: {DISPLAY_REFRESH}초)")
        logger.info(f"대상: {len(SERVICES)}개 서비스")
        logger.info(f"스크래퍼 무응답 임계값: {self.config.scrapper_timeout}초")
        logger.info(f"연속 에러 임계값: {self.config.consecutive_error_threshold}회")

        # 디스플레이 스레드 시작
        display_thread = threading.Thread(target=self._display_loop, daemon=True)
        display_thread.start()

        # 시작 시 즉시 1회 체크
        self.check_all()

        while self.running:
            # 체크 간격 대기 (버튼으로 즉시 체크 가능)
            triggered = self._force_check.wait(timeout=self.config.check_interval)
            if triggered:
                self._force_check.clear()

            if self.running:
                self.check_all()

    def stop(self):
        """종료"""
        self.running = False
        self._force_check.set()  # 대기 중인 wait() 해제
        self.state.close()
        logger.info("모니터링 종료")


def main():
    monitor = Monitor()

    def shutdown(*_):
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.stop()
    except Exception as e:
        logger.exception("예기치 않은 오류")
        monitor.display.set_system_error(f"프로그램 오류: {e}")
        monitor.stop()


if __name__ == "__main__":
    main()
