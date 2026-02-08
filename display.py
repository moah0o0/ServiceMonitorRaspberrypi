"""
PiTFT 디스플레이 (240x240)
Pillow로 이미지 생성 → SPI로 전송
라즈베리파이가 아닌 환경에서는 자동 비활성화

- 4개씩 페이지 자동 넘김 (5초 간격)
- 에러 서비스는 항상 우선 표시
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# 색상
COLOR_BG = (0, 0, 0)
COLOR_BG_ERROR = (60, 0, 0)
COLOR_OK = (0, 200, 0)
COLOR_ERROR = (255, 50, 50)
COLOR_WARN = (220, 180, 0)
COLOR_TEXT = (255, 255, 255)
COLOR_DIM = (120, 120, 120)
COLOR_SYS_ERROR = (255, 60, 60)

DISPLAY_WIDTH = 240
DISPLAY_HEIGHT = 240
ITEMS_PER_PAGE = 4
PAGE_INTERVAL = 5


class Display:
    def __init__(self):
        self.enabled = False
        self.disp = None
        self._font = None
        self._font_sm = None
        self._font_lg = None

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
                x_offset=0, y_offset=80,
            )
            self.enabled = True
            logger.info("PiTFT 디스플레이 초기화 성공")

            # 폰트 캐싱 (한 번만 로드)
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 14)
                self._font_sm = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 11)
                self._font_lg = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 18)
            except Exception:
                try:
                    self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
                    self._font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
                    self._font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
                except Exception:
                    self._font = ImageFont.load_default()
                    self._font_sm = self._font
                    self._font_lg = self._font

        except (ImportError, Exception) as e:
            logger.info(f"PiTFT 사용 불가 (로컬 환경): {e}")

        # 버튼 (GPIO 23, 24) - 인터럽트 방식
        self._button_pressed = False
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(23, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(24, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(23, GPIO.FALLING, callback=self._on_button, bouncetime=300)
            GPIO.add_event_detect(24, GPIO.FALLING, callback=self._on_button, bouncetime=300)
            logger.info("GPIO 버튼 인터럽트 등록 완료")
        except Exception as e:
            logger.info(f"GPIO 버튼 사용 불가: {e}")

        self._page = 0
        self._tick = 0
        self._need_refresh = True
        self._system_error: str | None = None

    def set_system_error(self, message: str | None):
        """시스템 에러 설정 (None이면 해제)"""
        if self._system_error != message:
            self._system_error = message
            self._need_refresh = True

    def advance_tick(self):
        """1초마다 호출. 페이지 자동 넘김 타이머."""
        self._tick += 1
        if self._tick >= PAGE_INTERVAL:
            self._tick = 0
            self._page += 1
            self._need_refresh = True

    def mark_dirty(self):
        """데이터 변경 시 호출 (체크 완료 후)"""
        self._need_refresh = True

    def update(self, states: list[dict]):
        """화면 갱신 (변경 있을 때만)"""
        if not self.enabled or not self._need_refresh:
            return
        self._need_refresh = False

        from PIL import Image, ImageDraw

        img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), COLOR_BG)
        draw = ImageDraw.Draw(img)

        font = self._font
        font_sm = self._font_sm

        # 에러 분류
        errors = [s for s in states if s["status"] != "ok"]
        oks = [s for s in states if s["status"] == "ok"]
        has_errors = len(errors) > 0

        if has_errors:
            draw.rectangle([(0, 0), (DISPLAY_WIDTH, DISPLAY_HEIGHT)], fill=COLOR_BG_ERROR)

        now = datetime.now(KST).strftime("%m/%d %H:%M:%S")
        draw.text((5, 5), now, fill=COLOR_TEXT, font=font)

        # 시스템 에러
        if self._system_error:
            draw.text((140, 5), "SYSTEM", fill=COLOR_SYS_ERROR, font=font)
            draw.line([(0, 25), (240, 25)], fill=COLOR_SYS_ERROR, width=1)
            self._draw_system_error(draw, self._system_error, font, font_sm)
            self.disp.image(img)
            return

        if has_errors:
            draw.text((140, 5), f"ERR {len(errors)}", fill=COLOR_ERROR, font=font)
            draw.line([(0, 25), (240, 25)], fill=COLOR_ERROR, width=1)
        else:
            draw.text((140, 5), f"{len(oks)}/{len(states)} OK", fill=COLOR_OK, font=font)
            draw.line([(0, 25), (240, 25)], fill=COLOR_DIM, width=1)

        sorted_states = errors + oks
        total_pages = max(1, (len(sorted_states) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = self._page % total_pages
        start = page * ITEMS_PER_PAGE
        page_items = sorted_states[start:start + ITEMS_PER_PAGE]

        self._draw_page(draw, page_items, font, font_sm)
        self._draw_page_indicator(draw, page, total_pages)

        self.disp.image(img)

    def _draw_page(self, draw, items: list[dict], font, font_sm):
        """4개 서비스 표시"""
        y = 32
        for s in items:
            is_error = s["status"] != "ok"
            color = COLOR_ERROR if is_error else COLOR_OK

            draw.ellipse([(5, y + 3), (17, y + 15)], fill=color)
            draw.text((22, y), s["name"][:15], fill=COLOR_TEXT, font=font)
            y += 20

            msg = s.get("message", "")[:25]
            draw.text((22, y), msg, fill=COLOR_WARN if is_error else COLOR_DIM, font=font_sm)
            y += 28

    def _draw_page_indicator(self, draw, current: int, total: int):
        """하단 페이지 도트"""
        y = 225
        dot_size = 6
        gap = 14
        total_width = total * dot_size + (total - 1) * (gap - dot_size)
        x = (DISPLAY_WIDTH - total_width) // 2
        for i in range(total):
            fill = COLOR_TEXT if i == current else COLOR_DIM
            draw.ellipse([(x, y), (x + dot_size, y + dot_size)], fill=fill)
            x += gap

    def _draw_system_error(self, draw, message: str, font, font_sm):
        """시스템 에러 전체 화면"""
        draw.rectangle([(10, 40), (230, 140)], outline=COLOR_SYS_ERROR, width=2)
        draw.text((60, 55), "! ERROR !", fill=COLOR_SYS_ERROR, font=self._font_lg)
        lines = []
        while message:
            lines.append(message[:26])
            message = message[26:]
        y = 90
        for line in lines[:3]:
            draw.text((20, y), line, fill=COLOR_TEXT, font=font)
            y += 16
        draw.text((30, 170), "자동 재시도 대기 중...", fill=COLOR_DIM, font=font_sm)

    def _on_button(self, channel):
        """GPIO 인터럽트 콜백 (즉시 반응)"""
        self._button_pressed = True

    def check_buttons(self) -> dict:
        """버튼 눌림 확인 (인터럽트로 감지된 것 처리)"""
        result = {"page_change": False}
        if self._button_pressed:
            self._button_pressed = False
            self._page += 1
            self._tick = 0
            self._need_refresh = True
            result["page_change"] = True
        return result
