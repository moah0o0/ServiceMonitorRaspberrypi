"""
서비스 체크 로직
HTTP / PocketBase Admin / 스크래퍼 상태 점검
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from config import CheckType, Config, Service, ScrapperConfig

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
REQUEST_TIMEOUT = 15


@dataclass
class CheckResult:
    """체크 결과"""
    service_name: str
    ok: bool
    message: str
    checked_at: datetime


def check_http(service: Service) -> CheckResult:
    """일반 웹사이트 HTTP 상태 체크 (200~399)"""
    now = datetime.now(KST)
    try:
        resp = requests.get(service.url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if 200 <= resp.status_code < 400:
            return CheckResult(service.name, True, f"HTTP {resp.status_code}", now)
        return CheckResult(service.name, False, f"HTTP {resp.status_code}", now)
    except requests.RequestException as e:
        return CheckResult(service.name, False, f"접속 실패: {e}", now)


def check_pb_admin(service: Service) -> CheckResult:
    """PocketBase Admin 페이지 체크 (/_/)"""
    now = datetime.now(KST)
    url = service.url.rstrip("/") + "/_/"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code == 200:
            return CheckResult(service.name, True, "PB Admin OK", now)
        return CheckResult(service.name, False, f"PB Admin HTTP {resp.status_code}", now)
    except requests.RequestException as e:
        return CheckResult(service.name, False, f"PB 접속 실패: {e}", now)


class PocketBaseAuth:
    """PocketBase 인증 토큰 관리 (서버별 캐싱)"""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._tokens: dict[str, str] = {}  # pb_url -> token

    def get_token(self, pb_url: str) -> Optional[str]:
        if pb_url in self._tokens:
            return self._tokens[pb_url]
        return self._authenticate(pb_url)

    def _authenticate(self, pb_url: str) -> Optional[str]:
        try:
            resp = requests.post(
                f"{pb_url}/api/collections/users/auth-with-password",
                json={"identity": self.email, "password": self.password},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            token = resp.json().get("token")
            if token:
                self._tokens[pb_url] = token
            return token
        except Exception as e:
            logger.error(f"PB 인증 실패 ({pb_url}): {e}")
            return None

    def invalidate(self, pb_url: str):
        self._tokens.pop(pb_url, None)


def _pb_get(auth: PocketBaseAuth, pb_url: str, endpoint: str, params: dict) -> Optional[dict]:
    """PocketBase API GET 요청 (토큰 만료 시 재인증 1회)"""
    for attempt in range(2):
        token = auth.get_token(pb_url)
        if not token:
            return None
        try:
            resp = requests.get(
                f"{pb_url}/api/collections/{endpoint}/records",
                params=params,
                headers={"Authorization": token},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 401 and attempt == 0:
                auth.invalidate(pb_url)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"PB API 요청 실패 ({endpoint}): {e}")
            return None
    return None


def _parse_pb_time(time_str: str) -> Optional[datetime]:
    """PocketBase 시간 문자열 파싱 (UTC 'Z' suffix 또는 KST 'YYYY-MM-DD HH:MM:SS')"""
    if not time_str:
        return None
    for fmt in [
        "%Y-%m-%d %H:%M:%S.%fZ",   # 2026-02-08 09:59:47.957Z
        "%Y-%m-%d %H:%M:%SZ",       # 2026-02-08 09:59:47Z
        "%Y-%m-%d %H:%M:%S.%f",     # 2026-02-08 09:59:47.957
        "%Y-%m-%d %H:%M:%S",        # 2026-02-08 19:01:33  (KST from heartbeat)
        "%Y-%m-%dT%H:%M:%S.%f%z",   # ISO format
        "%Y-%m-%dT%H:%M:%S%z",
    ]:
        try:
            dt = datetime.strptime(time_str, fmt)
            if dt.tzinfo is None:
                # UTC 'Z' suffix 케이스
                if time_str.endswith("Z"):
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    # KST로 저장된 heartbeat (last_ping: "2026-02-08 19:01:33")
                    dt = dt.replace(tzinfo=KST)
            return dt
        except ValueError:
            continue
    logger.warning(f"시간 파싱 실패: {time_str}")
    return None


def check_scrapper(service: Service, auth: PocketBaseAuth, config: Config) -> CheckResult:
    """스크래퍼 상태 체크 (PocketBase 로그/Heartbeat 기반)"""
    now = datetime.now(KST)
    sc = service.scrapper
    if not sc:
        return CheckResult(service.name, False, "스크래퍼 설정 없음", now)

    # 1) 최근 레코드에서 시간 확인
    sort_field = f"-{sc.time_field}"
    data = _pb_get(auth, sc.pb_url, sc.collection, {"sort": sort_field, "perPage": "1"})
    if not data or not data.get("items"):
        return CheckResult(service.name, False, f"{sc.collection} 데이터 없음", now)

    latest = data["items"][0]
    last_time = _parse_pb_time(latest.get(sc.time_field, ""))
    if not last_time:
        return CheckResult(service.name, False, f"{sc.time_field} 파싱 실패", now)

    # 시간 차이 계산
    elapsed = (now - last_time.astimezone(KST)).total_seconds()
    elapsed_min = int(elapsed // 60)

    # 30분(설정값) 초과 시 무응답 경고
    if elapsed > config.scrapper_timeout:
        return CheckResult(
            service.name, False,
            f"무응답 {elapsed_min}분 (마지막: {last_time.astimezone(KST).strftime('%H:%M')})",
            now,
        )

    # 2) Heartbeat status=stopped 체크 (BQA 스크래퍼)
    if sc.status_field:
        status_val = latest.get(sc.status_field)
        if status_val == "stopped":
            return CheckResult(service.name, False, f"스크래퍼 상태: stopped", now)

    # 3) 에러 로그 체크 (최근 2건이 연속 에러인지)
    if sc.error_collection and sc.error_level_field:
        err_data = _pb_get(auth, sc.pb_url, sc.error_collection, {
            "sort": "-created",
            "perPage": "2",
            "filter": f'{sc.error_level_field}="ERROR" || {sc.error_level_field}="error"',
        })
        if err_data and err_data.get("items"):
            err_items = err_data["items"]
            # 최근 에러가 5분 이내이면 보고
            if err_items:
                err_time = _parse_pb_time(err_items[0].get("created", ""))
                if err_time:
                    err_elapsed = (now - err_time.astimezone(KST)).total_seconds()
                    if err_elapsed < 300:  # 5분 이내 에러
                        err_msg = err_items[0].get("message", "")[:80]
                        return CheckResult(
                            service.name, False,
                            f"최근 에러: {err_msg}",
                            now,
                        )

    # 4) 구청별 메트릭 체크 (최근 2회 연속 실패 구청 감지)
    if sc.metrics_collection:
        failed = _check_district_metrics(auth, sc.pb_url, sc.metrics_collection)
        if failed:
            parts = [f"{name}({err[:50]})" for name, err in failed]
            return CheckResult(
                service.name, False,
                f"연속 실패 구청: {'; '.join(parts)}",
                now,
            )

    return CheckResult(service.name, True, f"정상 (마지막: {elapsed_min}분 전)", now)


def _check_district_metrics(auth: PocketBaseAuth, pb_url: str, collection: str) -> list[tuple[str, str]]:
    """최근 2회 메트릭에서 연속 실패 중인 구청 목록 반환. [(구청명, 에러메시지), ...]"""
    data = _pb_get(auth, pb_url, collection, {"sort": "-created", "perPage": "2"})
    if not data or len(data.get("items", [])) < 2:
        return []

    # 최근 2회 모두 실패한 구청 찾기
    failed_sets = []
    for record in data["items"]:
        results = record.get("district_results", [])
        if isinstance(results, list):
            failed = {r["district"] for r in results if not r.get("success", True)}
            failed_sets.append(failed)

    if len(failed_sets) < 2:
        return []

    # 2회 연속 실패 = 교집합
    persistent = failed_sets[0] & failed_sets[1]

    # 최신 메트릭에서 에러 메시지 추출
    latest_results = data["items"][0].get("district_results", [])
    result = []
    for r in latest_results:
        if r.get("district") in persistent:
            err = r.get("error_message") or "원인 불명"
            result.append((r["district"], err))

    return sorted(result, key=lambda x: x[0])


def check_service(service: Service, auth: PocketBaseAuth, config: Config) -> CheckResult:
    """서비스 유형에 따라 적절한 체크 수행"""
    if service.check_type == CheckType.HTTP:
        return check_http(service)
    elif service.check_type == CheckType.PB_ADMIN:
        return check_pb_admin(service)
    elif service.check_type == CheckType.SCRAPPER:
        return check_scrapper(service, auth, config)
    return CheckResult(service.name, False, f"알 수 없는 체크 유형: {service.check_type}", datetime.now(KST))
