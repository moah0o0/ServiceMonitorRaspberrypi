"""
서비스 모니터링 설정
모니터링 대상 서비스 목록 및 환경변수 관리
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

# 스크립트 위치 기준으로 .env 로드 (어디서 실행하든 동일하게 적용)
BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


class CheckType(Enum):
    HTTP = "http"
    PB_ADMIN = "pb_admin"
    SCRAPPER = "scrapper"


@dataclass
class ScrapperConfig:
    """스크래퍼 체크에 필요한 추가 설정"""
    pb_url: str
    collection: str
    time_field: str
    error_collection: Optional[str] = None
    error_level_field: Optional[str] = None
    status_field: Optional[str] = None  # HeartBeat용 (status=stopped 감지)
    metrics_collection: Optional[str] = None  # 구청별 성공/실패 메트릭 (BAPC용)


@dataclass
class Service:
    """모니터링 대상 서비스"""
    name: str
    check_type: CheckType
    url: str
    group: str = ""
    scrapper: Optional[ScrapperConfig] = None


@dataclass
class Config:
    """전체 설정"""
    pb_monitor_email: str = ""
    pb_monitor_password: str = ""
    check_interval: int = 300        # 5분
    scrapper_timeout: int = 1800     # 30분
    consecutive_error_threshold: int = 2
    db_path: str = str(BASE_DIR / "monitor.db")


def load_config() -> Config:
    return Config(
        pb_monitor_email=os.getenv("PB_MONITOR_EMAIL", ""),
        pb_monitor_password=os.getenv("PB_MONITOR_PASSWORD", ""),
        check_interval=int(os.getenv("CHECK_INTERVAL", "300")),
        scrapper_timeout=int(os.getenv("SCRAPPER_TIMEOUT", "1800")),
        consecutive_error_threshold=int(os.getenv("CONSECUTIVE_ERROR_THRESHOLD", "2")),
    )


# ========== 모니터링 대상 서비스 목록 ==========

SERVICES = [
    # --- Scrapper (PocketBase 로그 기반) ---
    Service(
        name="BAPC 공영장례 스크래퍼",
        check_type=CheckType.SCRAPPER,
        url="https://public-funeral-api.bapc.kr",
        group="부산반빈곤센터",
        scrapper=ScrapperConfig(
            pb_url="https://public-funeral-api.bapc.kr",
            collection="scraper_log",
            time_field="logged_at",
            error_collection="scraper_log",
            error_level_field="level",
            metrics_collection="scraper_metrics",
        ),
    ),
    Service(
        name="BQA 은행거래 스크래퍼",
        check_type=CheckType.SCRAPPER,
        url="https://api-money.busanqueeract.kr",
        group="부산퀴어행동",
        scrapper=ScrapperConfig(
            pb_url="https://api-money.busanqueeract.kr",
            collection="ScrapperHeartbeat",
            time_field="last_ping",
            status_field="status",
            error_collection="ScrapperLog",
            error_level_field="level",
        ),
    ),

    # --- Pocketbase Admin (/_/ 200 OK) ---
    Service(
        name="BAPC 웹사이트 DB",
        check_type=CheckType.PB_ADMIN,
        url="https://site-api.bapc.kr",
        group="부산반빈곤센터",
    ),
    Service(
        name="BAPC 공영장례 DB",
        check_type=CheckType.PB_ADMIN,
        url="https://public-funeral-api.bapc.kr",
        group="부산반빈곤센터",
    ),
    Service(
        name="BQA 공금관리 DB",
        check_type=CheckType.PB_ADMIN,
        url="https://api-money.busanqueeract.kr",
        group="부산퀴어행동",
    ),
    Service(
        name="만원의연대 웹사이트 DB",
        check_type=CheckType.PB_ADMIN,
        url="https://site-api.manwon2013.co.kr",
        group="만원의연대",
    ),

    # --- 일반 웹사이트 (HTTP 200~399) ---
    Service(
        name="BAPC 단체 홈페이지",
        check_type=CheckType.HTTP,
        url="https://bapc.kr",
        group="부산반빈곤센터",
    ),
    Service(
        name="BAPC 공영장례 추모 페이지",
        check_type=CheckType.HTTP,
        url="https://obit.bapc.kr",
        group="부산반빈곤센터",
    ),
    Service(
        name="BAPC 스크래퍼 모니터",
        check_type=CheckType.HTTP,
        url="https://public-funeral-monitor.bapc.kr",
        group="부산반빈곤센터",
    ),
    Service(
        name="BQA 소개 페이지",
        check_type=CheckType.HTTP,
        url="https://about.busanqueeract.kr",
        group="부산퀴어행동",
    ),
    Service(
        name="BQA 공금관리 페이지",
        check_type=CheckType.HTTP,
        url="https://money.busanqueeract.kr",
        group="부산퀴어행동",
    ),
    Service(
        name="BQA 할일관리",
        check_type=CheckType.HTTP,
        url="https://todo.busanqueeract.kr",
        group="부산퀴어행동",
    ),
    Service(
        name="양산외국인노동자의집 홈페이지",
        check_type=CheckType.HTTP,
        url="https://withmigrant.or.kr",
        group="양산외국인노동자의집",
    ),
    Service(
        name="만원의연대 홈페이지",
        check_type=CheckType.HTTP,
        url="https://manwon2013.co.kr",
        group="만원의연대",
    ),
]
