"""
PiTFT 디스플레이 (240x240)
Pillow로 이미지 생성 -> SPI로 전송
라즈베리파이가 아닌 환경에서는 자동 비활성화

최소 폰트 22pt / 빠른 페이지 전환 / 에러 강조
- 페이지 0: 대시보드 (큰 원 + 상태)
- 페이지 1+: 서비스 리스트 (5개씩, 에러 우선)
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
LIST_PER_PAGE = 5
PAGE_INTERVAL = 3  # 3초마다 자동 페이지 전환


class Display:
    def __init__(self):
        self.enabled = False
        self.disp = None
        self._font = None
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

            # 폰트: 최소 22pt
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 22)
                self._font_lg = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 30)
                self._font_xl = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 40)
            except Exception:
                try:
                    self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
                    self._font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
                    self._font_xl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
                except Exception:
                    self._font = ImageFont.load_default()
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

    def _truncate(self, draw, text: str, font, max_width: int) -> str:
        """텍스트가 max_width 픽셀을 넘지 않도록 자르기"""
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return text
        while len(text) > 1:
            text = text[:-1]
            bbox = draw.textbbox((0, 0), text, font=font)
            if bbox[2] - bbox[0] <= max_width:
                return text
        return text

    def update(self, states: list[dict]):
        if not self.enabled or not self._need_refresh:
            return
        self._need_refresh = False

        from PIL import Image, ImageDraw

        errors = [s for s in states if s["status"] != "ok"]
        oks = [s for s in states if s["status"] == "ok"]
        has_errors = len(errors) > 0
        sorted_states = errors + oks

        list_pages = max(1, (len(sorted_states) + LIST_PER_PAGE - 1) // LIST_PER_PAGE)
        total_pages = 1 + list_pages
        page = self._page % total_pages

        img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), COLOR_BG)
        draw = ImageDraw.Draw(img)

        if has_errors:
            draw.rectangle([(0, 0), (DISPLAY_WIDTH, DISPLAY_HEIGHT)], fill=COLOR_BG_ERROR)

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
        """대시보드: 큰 원 + 숫자"""
        ok_count = len(oks)
        has_errors = len(errors) > 0

        # 시간
        now = datetime.now(KST).strftime("%H:%M")
        bbox = draw.textbbox((0, 0), now, font=self._font)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, 4), now, fill=COLOR_DIM, font=self._font)

        # 큰 원
        cx, cy = 120, 110
        r = 65
        circle_color = COLOR_CIRCLE_ERR if has_errors else COLOR_CIRCLE_OK
        for offset in range(4):
            draw.ellipse(
                [(cx - r - offset, cy - r - offset), (cx + r + offset, cy + r + offset)],
                outline=circle_color, width=3,
            )

        # 원 안의 숫자
        count_text = f"{ok_count}/{total}"
        bbox = draw.textbbox((0, 0), count_text, font=self._font_xl)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((DISPLAY_WIDTH - tw) // 2, cy - th - 4), count_text, fill=COLOR_TEXT, font=self._font_xl)

        # UP / ERR
        status_text = "ERR" if has_errors else "UP"
        status_color = COLOR_ERROR if has_errors else COLOR_OK
        bbox = draw.textbbox((0, 0), status_text, font=self._font_lg)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, cy + 8), status_text, fill=status_color, font=self._font_lg)

        # 에러 서비스 (원 아래, 최대 2개)
        if has_errors:
            y = 190
            for s in errors[:2]:
                label = self._truncate(draw, f"X {s['name']}", self._font, 228)
                draw.text((6, y), label, fill=COLOR_ERROR, font=self._font)
                y += 28

        self._draw_page_indicator(draw, 0, total_pages)

    def _draw_list(self, draw, items: list[dict], errors: list, oks: list, total: int, has_errors: bool):
        """서비스 리스트 (5개씩)"""
        ok_count = len(oks)

        # 헤더: 상태 요약 (ERR=빨강, OK=초록 분리)
        if has_errors:
            err_text = f"ERR {len(errors)}"
            draw.text((8, 4), err_text, fill=COLOR_ERROR, font=self._font)
            bbox = draw.textbbox((0, 0), err_text + " ", font=self._font)
            ok_x = 8 + bbox[2] - bbox[0]
            draw.text((ok_x, 4), f"OK {ok_count}", fill=COLOR_OK, font=self._font)
        else:
            draw.text((8, 4), f"ALL {total} OK", fill=COLOR_OK, font=self._font)

        draw.line([(0, 32), (240, 32)], fill=COLOR_DIM, width=1)

        # 서비스 행
        y = 36
        row_h = 38
        for s in items:
            is_error = s["status"] != "ok"

            # 상태 도트
            dot_color = COLOR_ERROR if is_error else COLOR_OK
            draw.ellipse([(6, y + 8), (20, y + 22)], fill=dot_color)

            # 서비스 이름 (히스토리 바 전까지 맞춤)
            name = self._truncate(draw, s["name"], self._font, 140)
            draw.text((26, y + 4), name, fill=COLOR_TEXT, font=self._font)

            # 히스토리 바 (우측)
            history = s.get("history", [])
            self._draw_history_bar(draw, 175, y + 6, history)

            y += row_h

    def _draw_history_bar(self, draw, x: int, y: int, history: list[str]):
        """최근 히스토리 블록 (8칸)"""
        block_w = 6
        block_h = 18
        gap = 2
        slots = 8

        for i in range(slots):
            bx = x + i * (block_w + gap)
            if i < len(history):
                color = COLOR_HISTORY_OK if history[i] == "ok" else COLOR_HISTORY_ERR
            else:
                color = COLOR_HISTORY_EMPTY
            draw.rectangle([(bx, y), (bx + block_w, y + block_h)], fill=color)

    def _draw_page_indicator(self, draw, current: int, total: int):
        """하단 페이지 도트"""
        y = 230
        dot_size = 8
        gap = 16
        total_width = total * dot_size + (total - 1) * (gap - dot_size)
        x = (DISPLAY_WIDTH - total_width) // 2
        for i in range(total):
            fill = COLOR_TEXT if i == current else COLOR_DIM
            draw.ellipse([(x, y), (x + dot_size, y + dot_size)], fill=fill)
            x += gap

    def _draw_system_error(self, draw, message: str):
        """시스템 에러 전체 화면"""
        # ERROR 타이틀
        bbox = draw.textbbox((0, 0), "ERROR", font=self._font_xl)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, 30), "ERROR", fill=COLOR_SYS_ERROR, font=self._font_xl)

        # 에러 내용
        draw.rectangle([(8, 85), (232, 180)], outline=COLOR_SYS_ERROR, width=2)
        lines = []
        while message:
            line = self._truncate(draw, message, self._font, 208)
            lines.append(line)
            message = message[len(line):]
        y = 95
        for line in lines[:3]:
            draw.text((16, y), line, fill=COLOR_TEXT, font=self._font)
            y += 28

        draw.text((20, 200), "재시도 대기중...", fill=COLOR_DIM, font=self._font)

    def check_buttons(self) -> dict:
        """버튼 폴링: HIGH->LOW 전환 감지"""
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
