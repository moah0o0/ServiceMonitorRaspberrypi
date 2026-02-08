"""
PiTFT 디스플레이 (240x240)
Pillow로 이미지 생성 → SPI로 전송
라즈베리파이가 아닌 환경에서는 자동 비활성화

- 4개씩 페이지 자동 넘김 (5초 간격)
- 에러 서비스는 항상 우선 표시
"""

import logging
from datetime import datetime, timezone, timedelta

try:
    from PIL import ImageFont
except ImportError:
    ImageFont = None

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# 색상
COLOR_BG = (0, 0, 0)
COLOR_OK = (0, 200, 0)
COLOR_ERROR = (220, 30, 30)
COLOR_WARN = (220, 180, 0)
COLOR_TEXT = (255, 255, 255)
COLOR_DIM = (120, 120, 120)
COLOR_SYS_ERROR = (255, 60, 60)

DISPLAY_WIDTH = 240
DISPLAY_HEIGHT = 240
ITEMS_PER_PAGE = 4
PAGE_INTERVAL = 5  # 페이지 자동 넘김 간격 (초)


class Display:
    def __init__(self):
        self.enabled = False
        self.disp = None
        try:
            import board
            import digitalio
            from adafruit_rgb_display.st7789 import ST7789
            from PIL import Image, ImageDraw, ImageFont

            cs_pin = digitalio.DigitalInOut(board.CE0)
            dc_pin = digitalio.DigitalInOut(board.D25)
            reset_pin = None
            BAUDRATE = 64000000
            spi = board.SPI()
            self.disp = ST7789(
                spi, cs=cs_pin, dc=dc_pin, rst=reset_pin,
                baudrate=BAUDRATE, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
                x_offset=0, y_offset=80,
            )
            self.enabled = True
            logger.info("PiTFT 디스플레이 초기화 성공")
        except (ImportError, Exception) as e:
            logger.info(f"PiTFT 사용 불가 (로컬 환경): {e}")

        # 버튼 (GPIO 23, 24)
        self.buttons_enabled = False
        self.button_a = None
        self.button_b = None
        try:
            import board
            import digitalio
            self.button_a = digitalio.DigitalInOut(board.D23)
            self.button_a.switch_to_input()
            self.button_b = digitalio.DigitalInOut(board.D24)
            self.button_b.switch_to_input()
            self.buttons_enabled = True
        except Exception:
            pass

        self._page = 0
        self._tick = 0
        self._system_error: str | None = None

    def set_system_error(self, message: str | None):
        """시스템 에러 설정 (None이면 해제)"""
        self._system_error = message

    def advance_tick(self):
        """1초마다 호출. 페이지 자동 넘김 타이머."""
        self._tick += 1
        if self._tick >= PAGE_INTERVAL:
            self._tick = 0
            self._page += 1

    def update(self, states: list[dict]):
        """화면 갱신"""
        if not self.enabled:
            return

        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), COLOR_BG)
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 14)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 11)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
                font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
            except Exception:
                font = ImageFont.load_default()
                font_sm = font

        # 상단: 시간 + 요약
        now = datetime.now(KST).strftime("%m/%d %H:%M:%S")
        ok_count = sum(1 for s in states if s["status"] == "ok")
        total = len(states)
        summary_color = COLOR_OK if ok_count == total else COLOR_ERROR

        draw.text((5, 5), now, fill=COLOR_TEXT, font=font)

        # 시스템 에러
        if self._system_error:
            draw.text((140, 5), "SYSTEM", fill=COLOR_SYS_ERROR, font=font)
            draw.line([(0, 25), (240, 25)], fill=COLOR_SYS_ERROR, width=1)
            self._draw_system_error(draw, self._system_error, font, font_sm)
            self.disp.image(img)
            return

        draw.text((140, 5), f"{ok_count}/{total} OK", fill=summary_color, font=font)
        draw.line([(0, 25), (240, 25)], fill=COLOR_DIM, width=1)

        # 에러 우선 정렬: 에러 → 정상 순서
        errors = [s for s in states if s["status"] != "ok"]
        oks = [s for s in states if s["status"] == "ok"]
        sorted_states = errors + oks

        # 페이지 계산
        total_pages = max(1, (len(sorted_states) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = self._page % total_pages
        start = page * ITEMS_PER_PAGE
        page_items = sorted_states[start:start + ITEMS_PER_PAGE]

        # 서비스 4개 표시
        self._draw_page(draw, page_items, font, font_sm)

        # 하단: 페이지 인디케이터
        self._draw_page_indicator(draw, page, total_pages, font_sm)

        self.disp.image(img)

    def _draw_page(self, draw, items: list[dict], font, font_sm):
        """4개 서비스 표시 (이름 + 상태 메시지)"""
        y = 32
        for s in items:
            is_error = s["status"] != "ok"
            color = COLOR_ERROR if is_error else COLOR_OK

            # 상태 도트 (크게)
            draw.ellipse([(5, y + 3), (17, y + 15)], fill=color)

            # 서비스명
            name = s["name"][:15]
            draw.text((22, y), name, fill=COLOR_TEXT, font=font)
            y += 20

            # 상태 메시지
            msg = s.get("message", "")[:25]
            msg_color = COLOR_WARN if is_error else COLOR_DIM
            draw.text((22, y), msg, fill=msg_color, font=font_sm)
            y += 28

    def _draw_page_indicator(self, draw, current: int, total: int, font):
        """하단 페이지 표시 (● ○ ○ ○)"""
        y = 225
        dot_size = 6
        gap = 14
        total_width = total * dot_size + (total - 1) * (gap - dot_size)
        x = (DISPLAY_WIDTH - total_width) // 2

        for i in range(total):
            if i == current:
                draw.ellipse([(x, y), (x + dot_size, y + dot_size)], fill=COLOR_TEXT)
            else:
                draw.ellipse([(x, y), (x + dot_size, y + dot_size)], fill=COLOR_DIM)
            x += gap

    def _draw_system_error(self, draw, message: str, font, font_sm):
        """시스템 에러 전체 화면 표시"""
        draw.rectangle([(10, 40), (230, 140)], outline=COLOR_SYS_ERROR, width=2)
        try:
            font_lg = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 18)
        except Exception:
            font_lg = font

        draw.text((60, 55), "! ERROR !", fill=COLOR_SYS_ERROR, font=font_lg)
        lines = []
        while message:
            lines.append(message[:26])
            message = message[26:]
        y = 90
        for line in lines[:3]:
            draw.text((20, y), line, fill=COLOR_TEXT, font=font)
            y += 16

        draw.text((30, 170), "자동 재시도 대기 중...", fill=COLOR_DIM, font=font_sm)

    def check_buttons(self) -> dict:
        """버튼 상태 확인. 반환: {"page_change": bool, "force_check": bool}"""
        result = {"page_change": False, "force_check": False}
        if not self.buttons_enabled:
            return result
        # Mini PiTFT 버튼은 눌리면 False (active low)
        if not self.button_a.value:
            self._page += 1
            self._tick = 0  # 수동 넘김 시 타이머 리셋
            result["page_change"] = True
        if not self.button_b.value:
            result["force_check"] = True
        return result
