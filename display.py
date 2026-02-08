"""
PiTFT 디스플레이 (240x240)
Pillow로 이미지 생성 → SPI로 전송
라즈베리파이가 아닌 환경에서는 자동 비활성화

- 페이지 0: 대시보드 (큰 원형 상태 + 에러 목록)
- 페이지 1+: 서비스 리스트 (히스토리 바 포함, 7개씩)
- 에러 서비스는 항상 우선 표시
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# 색상
COLOR_BG = (0, 0, 0)
COLOR_BG_ERROR = (50, 0, 0)
COLOR_OK = (0, 200, 0)
COLOR_ERROR = (255, 50, 50)
COLOR_WARN = (220, 180, 0)
COLOR_TEXT = (255, 255, 255)
COLOR_DIM = (80, 80, 80)
COLOR_SYS_ERROR = (255, 60, 60)
COLOR_CIRCLE_OK = (0, 160, 0)
COLOR_CIRCLE_ERR = (200, 30, 30)
COLOR_HISTORY_OK = (0, 150, 0)
COLOR_HISTORY_ERR = (200, 40, 40)
COLOR_HISTORY_EMPTY = (40, 40, 40)

DISPLAY_WIDTH = 240
DISPLAY_HEIGHT = 240
LIST_PER_PAGE = 7
PAGE_INTERVAL = 5


class Display:
    def __init__(self):
        self.enabled = False
        self.disp = None
        self._font = None
        self._font_sm = None
        self._font_lg = None
        self._font_xl = None

        try:
            import board
            import digitalio
            from adafruit_rgb_display.st7789 import ST7789
            from PIL import ImageFont

            cs_pin = digitalio.DigitalInOut(board.CE0)
            dc_pin = digitalio.DigitalInOut(board.D25)
            reset_pin = None
            spi = board.SPI()
            self.disp = ST7789(
                spi, cs=cs_pin, dc=dc_pin, rst=reset_pin,
                baudrate=64000000, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
                x_offset=0, y_offset=80, rotation=180,
            )
            self.enabled = True
            logger.info("PiTFT 디스플레이 초기화 성공")

            # 폰트 캐싱
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 13)
                self._font_sm = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 11)
                self._font_lg = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 18)
                self._font_xl = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 26)
            except Exception:
                try:
                    self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
                    self._font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
                    self._font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
                    self._font_xl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
                except Exception:
                    self._font = ImageFont.load_default()
                    self._font_sm = self._font
                    self._font_lg = self._font
                    self._font_xl = self._font

        except (ImportError, Exception) as e:
            logger.info(f"PiTFT 사용 불가 (로컬 환경): {e}")

        # 버튼 (GPIO 23, 24) - 폴링 방식
        self._gpio = None
        self._btn_prev = {23: True, 24: True}
        try:
            import RPi.GPIO as GPIO
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(23, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(24, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            self._gpio = GPIO
            logger.info("GPIO 버튼 폴링 모드 등록 완료")
        except Exception as e:
            logger.info(f"GPIO 버튼 사용 불가: {e}")

        self._page = 0
        self._tick = 0
        self._need_refresh = True
        self._system_error: str | None = None

    def set_system_error(self, message: str | None):
        if self._system_error != message:
            self._system_error = message
            self._need_refresh = True

    def advance_tick(self):
        self._tick += 1
        if self._tick >= PAGE_INTERVAL:
            self._tick = 0
            self._page += 1
            self._need_refresh = True

    def mark_dirty(self):
        self._need_refresh = True

    def update(self, states: list[dict]):
        if not self.enabled or not self._need_refresh:
            return
        self._need_refresh = False

        from PIL import Image, ImageDraw

        # 에러 분류
        errors = [s for s in states if s["status"] != "ok"]
        oks = [s for s in states if s["status"] == "ok"]
        has_errors = len(errors) > 0
        sorted_states = errors + oks

        # 페이지 계산: 페이지0=대시보드, 페이지1+=리스트
        list_pages = max(1, (len(sorted_states) + LIST_PER_PAGE - 1) // LIST_PER_PAGE)
        total_pages = 1 + list_pages  # 대시보드 + 리스트 페이지들
        page = self._page % total_pages

        img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), COLOR_BG)
        draw = ImageDraw.Draw(img)

        if has_errors:
            draw.rectangle([(0, 0), (DISPLAY_WIDTH, DISPLAY_HEIGHT)], fill=COLOR_BG_ERROR)

        # 시스템 에러 (최우선)
        if self._system_error:
            self._draw_system_error(draw, self._system_error)
            self.disp.image(img)
            return

        if page == 0:
            self._draw_dashboard(draw, errors, oks, len(states), total_pages)
        else:
            list_page = page - 1
            start = list_page * LIST_PER_PAGE
            page_items = sorted_states[start:start + LIST_PER_PAGE]
            self._draw_list(draw, page_items, errors, oks, len(states), has_errors)
            self._draw_page_indicator(draw, page, total_pages)

        self.disp.image(img)

    def _draw_dashboard(self, draw, errors: list, oks: list, total: int, total_pages: int):
        """페이지 0: 큰 원형 대시보드"""
        ok_count = len(oks)
        has_errors = len(errors) > 0

        # 시간 (상단 중앙)
        now = datetime.now(KST).strftime("%m/%d %H:%M:%S")
        bbox = draw.textbbox((0, 0), now, font=self._font)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, 6), now, fill=COLOR_DIM, font=self._font)

        # 큰 원
        cx, cy = 120, 95
        r = 52
        circle_color = COLOR_CIRCLE_ERR if has_errors else COLOR_CIRCLE_OK
        # 원 테두리 (두꺼운 원)
        for offset in range(3):
            draw.ellipse(
                [(cx - r - offset, cy - r - offset), (cx + r + offset, cy + r + offset)],
                outline=circle_color, width=3,
            )

        # 원 안의 숫자
        count_text = f"{ok_count}/{total}"
        bbox = draw.textbbox((0, 0), count_text, font=self._font_xl)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((DISPLAY_WIDTH - tw) // 2, cy - th - 2), count_text, fill=COLOR_TEXT, font=self._font_xl)

        # "UP" 또는 "ERR" 텍스트
        status_text = "ERR" if has_errors else "UP"
        status_color = COLOR_ERROR if has_errors else COLOR_OK
        bbox = draw.textbbox((0, 0), status_text, font=self._font_lg)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, cy + 5), status_text, fill=status_color, font=self._font_lg)

        # 에러 서비스 목록 (원 아래)
        if has_errors:
            y = 160
            for s in errors[:3]:
                draw.ellipse([(15, y + 2), (23, y + 10)], fill=COLOR_ERROR)
                name = s["name"][:18]
                draw.text((28, y - 2), name, fill=COLOR_TEXT, font=self._font_sm)
                msg = s.get("message", "")[:20]
                draw.text((28, y + 12), msg, fill=COLOR_WARN, font=self._font_sm)
                y += 28
            if len(errors) > 3:
                draw.text((28, y), f"+{len(errors) - 3}개 더...", fill=COLOR_DIM, font=self._font_sm)

        # 페이지 도트
        self._draw_page_indicator(draw, 0, total_pages)

    def _draw_list(self, draw, items: list[dict], errors: list, oks: list, total: int, has_errors: bool):
        """서비스 리스트 (히스토리 바 포함)"""
        ok_count = len(oks)

        # 헤더
        now = datetime.now(KST).strftime("%m/%d %H:%M")
        draw.text((5, 5), now, fill=COLOR_TEXT, font=self._font)

        if has_errors:
            status = f"ERR {len(errors)}"
            draw.text((155, 5), status, fill=COLOR_ERROR, font=self._font)
        else:
            status = f"{ok_count}/{total} OK"
            draw.text((155, 5), status, fill=COLOR_OK, font=self._font)

        draw.line([(0, 23), (240, 23)], fill=COLOR_DIM, width=1)

        # 서비스 행들
        y = 27
        row_h = 27
        for s in items:
            is_error = s["status"] != "ok"
            dot_color = COLOR_ERROR if is_error else COLOR_OK

            # 상태 도트
            draw.ellipse([(5, y + 5), (13, y + 13)], fill=dot_color)

            # 서비스 이름
            name = s["name"][:12]
            draw.text((17, y + 2), name, fill=COLOR_TEXT, font=self._font_sm)

            # 히스토리 바
            history = s.get("history", [])
            self._draw_history_bar(draw, 170, y + 3, history)

            y += row_h

    def _draw_history_bar(self, draw, x: int, y: int, history: list[str]):
        """최근 10회 체크 히스토리를 색상 블록으로 표시"""
        block_w = 5
        block_h = 12
        gap = 2
        slots = 10

        for i in range(slots):
            bx = x + i * (block_w + gap)
            if i < len(history):
                color = COLOR_HISTORY_OK if history[i] == "ok" else COLOR_HISTORY_ERR
            else:
                color = COLOR_HISTORY_EMPTY
            draw.rectangle([(bx, y), (bx + block_w, y + block_h)], fill=color)

    def _draw_page_indicator(self, draw, current: int, total: int):
        """하단 페이지 도트"""
        y = 228
        dot_size = 6
        gap = 14
        total_width = total * dot_size + (total - 1) * (gap - dot_size)
        x = (DISPLAY_WIDTH - total_width) // 2
        for i in range(total):
            fill = COLOR_TEXT if i == current else COLOR_DIM
            draw.ellipse([(x, y), (x + dot_size, y + dot_size)], fill=fill)
            x += gap

    def _draw_system_error(self, draw, message: str):
        """시스템 에러 전체 화면"""
        now = datetime.now(KST).strftime("%m/%d %H:%M:%S")
        bbox = draw.textbbox((0, 0), now, font=self._font)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, 6), now, fill=COLOR_DIM, font=self._font)

        draw.rectangle([(10, 40), (230, 150)], outline=COLOR_SYS_ERROR, width=2)
        # ! 아이콘
        bbox = draw.textbbox((0, 0), "! ERROR !", font=self._font_lg)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, 55), "! ERROR !", fill=COLOR_SYS_ERROR, font=self._font_lg)

        lines = []
        while message:
            lines.append(message[:26])
            message = message[26:]
        y = 90
        for line in lines[:3]:
            draw.text((20, y), line, fill=COLOR_TEXT, font=self._font)
            y += 16

        draw.text((30, 175), "자동 재시도 대기 중...", fill=COLOR_DIM, font=self._font_sm)

    def check_buttons(self) -> dict:
        """버튼 폴링: HIGH→LOW 전환 감지"""
        result = {"page_change": False}
        if not self._gpio:
            return result
        for pin in (23, 24):
            cur = self._gpio.input(pin)
            if self._btn_prev[pin] and not cur:
                self._page += 1
                self._tick = 0
                self._need_refresh = True
                result["page_change"] = True
            self._btn_prev[pin] = cur
        return result
