"""
PiTFT 디스플레이 (240x240)
최소 22pt Bold / 텍스트 줄바꿈 / 동적 페이지 분할

- 페이지 0: 대시보드 (큰 원 + 상태)
- 페이지 1+: 서비스 리스트 (높이에 맞게 동적 분할)
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
PAGE_INTERVAL = 3

# 리스트 레이아웃
LIST_TOP = 36       # 헤더 아래
LIST_BOTTOM = 222   # 페이지 도트 위
LINE_H = 26         # 텍스트 한 줄 높이
BAR_H = 14          # 히스토리 바 높이
ITEM_GAP = 6        # 서비스 간 간격
TEXT_AREA_W = 208    # 텍스트 영역 너비 (26~234)


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
                x_offset=0, y_offset=80, rotation=90,
            )
            self.enabled = True
            logger.info("PiTFT 디스플레이 초기화 성공")

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

    # ── 텍스트 유틸 ──

    def _wrap_text(self, draw, text: str, font, max_width: int) -> list[str]:
        """텍스트를 max_width 안에서 줄바꿈"""
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return [text]
        lines = []
        current = ""
        for ch in text:
            test = current + ch
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines or [text]

    def _item_height(self, num_lines: int) -> int:
        """서비스 아이템 1개의 총 높이"""
        return num_lines * LINE_H + BAR_H + ITEM_GAP

    # ── 동적 페이지 분할 ──

    def _paginate(self, draw, items: list[dict]) -> list[list]:
        """아이템들을 높이 기반으로 페이지 분할"""
        pages = []
        current_page = []
        y = LIST_TOP

        for s in items:
            lines = self._wrap_text(draw, s["name"], self._font, TEXT_AREA_W)
            h = self._item_height(len(lines))

            if y + h > LIST_BOTTOM and current_page:
                pages.append(current_page)
                current_page = []
                y = LIST_TOP

            current_page.append(s)
            y += h

        if current_page:
            pages.append(current_page)

        return pages or [[]]

    # ── 메인 업데이트 ──

    def update(self, states: list[dict]):
        if not self.enabled or not self._need_refresh:
            return
        self._need_refresh = False

        from PIL import Image, ImageDraw

        errors = [s for s in states if s["status"] != "ok"]
        oks = [s for s in states if s["status"] == "ok"]
        has_errors = len(errors) > 0
        sorted_states = errors + oks

        img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), COLOR_BG)
        draw = ImageDraw.Draw(img)

        if has_errors:
            draw.rectangle([(0, 0), (DISPLAY_WIDTH, DISPLAY_HEIGHT)], fill=COLOR_BG_ERROR)

        if self._system_error:
            self._draw_system_error(draw, self._system_error)
            self.disp.image(img)
            return

        # 동적 페이지 계산
        list_pages = self._paginate(draw, sorted_states)
        total_pages = 1 + len(list_pages)
        page = self._page % total_pages

        if page == 0:
            self._draw_dashboard(draw, errors, oks, len(states), total_pages)
        else:
            page_items = list_pages[page - 1]
            self._draw_list(draw, page_items, errors, oks, len(states), has_errors)
            self._draw_page_indicator(draw, page, total_pages)

        self.disp.image(img)

    # ── 대시보드 ──

    def _draw_dashboard(self, draw, errors: list, oks: list, total: int, total_pages: int):
        ok_count = len(oks)
        has_errors = len(errors) > 0

        now = datetime.now(KST).strftime("%H:%M")
        bbox = draw.textbbox((0, 0), now, font=self._font)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, 4), now, fill=COLOR_DIM, font=self._font)

        cx, cy = 120, 110
        r = 65
        circle_color = COLOR_CIRCLE_ERR if has_errors else COLOR_CIRCLE_OK
        for offset in range(4):
            draw.ellipse(
                [(cx - r - offset, cy - r - offset), (cx + r + offset, cy + r + offset)],
                outline=circle_color, width=3,
            )

        count_text = f"{ok_count}/{total}"
        bbox = draw.textbbox((0, 0), count_text, font=self._font_xl)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((DISPLAY_WIDTH - tw) // 2, cy - th - 4), count_text, fill=COLOR_TEXT, font=self._font_xl)

        status_text = "ERR" if has_errors else "UP"
        status_color = COLOR_ERROR if has_errors else COLOR_OK
        bbox = draw.textbbox((0, 0), status_text, font=self._font_lg)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, cy + 8), status_text, fill=status_color, font=self._font_lg)

        if has_errors:
            y = 190
            for s in errors[:2]:
                lines = self._wrap_text(draw, s["name"], self._font, 228)
                draw.text((6, y), "X " + lines[0], fill=COLOR_ERROR, font=self._font)
                y += 28

        self._draw_page_indicator(draw, 0, total_pages)

    # ── 서비스 리스트 (동적 높이) ──

    def _draw_list(self, draw, items: list[dict], errors: list, oks: list, total: int, has_errors: bool):
        ok_count = len(oks)

        if has_errors:
            err_text = f"ERR {len(errors)}"
            draw.text((8, 4), err_text, fill=COLOR_ERROR, font=self._font)
            bbox = draw.textbbox((0, 0), err_text + " ", font=self._font)
            ok_x = 8 + bbox[2] - bbox[0]
            draw.text((ok_x, 4), f"OK {ok_count}", fill=COLOR_OK, font=self._font)
        else:
            draw.text((8, 4), f"ALL {total} OK", fill=COLOR_OK, font=self._font)

        draw.line([(0, 32), (240, 32)], fill=COLOR_DIM, width=1)

        y = LIST_TOP
        for s in items:
            is_error = s["status"] != "ok"
            dot_color = COLOR_ERROR if is_error else COLOR_OK

            # 상태 도트 (첫 줄 기준)
            draw.ellipse([(6, y + 4), (20, y + 18)], fill=dot_color)

            # 서비스 이름 (줄바꿈)
            lines = self._wrap_text(draw, s["name"], self._font, TEXT_AREA_W)
            for line in lines:
                draw.text((26, y), line, fill=COLOR_TEXT, font=self._font)
                y += LINE_H

            # 히스토리 바
            history = s.get("history", [])
            self._draw_history_bar(draw, 26, y, history)
            y += BAR_H + ITEM_GAP

    # ── 히스토리 바 ──

    def _draw_history_bar(self, draw, x: int, y: int, history: list[str]):
        block_w = 20
        block_h = 12
        gap = 3
        slots = 8
        for i in range(slots):
            bx = x + i * (block_w + gap)
            if i < len(history):
                color = COLOR_HISTORY_OK if history[i] == "ok" else COLOR_HISTORY_ERR
            else:
                color = COLOR_HISTORY_EMPTY
            draw.rectangle([(bx, y), (bx + block_w, y + block_h)], fill=color)

    # ── 페이지 도트 ──

    def _draw_page_indicator(self, draw, current: int, total: int):
        y = 230
        dot_size = 8
        gap = 16
        total_width = total * dot_size + (total - 1) * (gap - dot_size)
        x = (DISPLAY_WIDTH - total_width) // 2
        for i in range(total):
            fill = COLOR_TEXT if i == current else COLOR_DIM
            draw.ellipse([(x, y), (x + dot_size, y + dot_size)], fill=fill)
            x += gap

    # ── 시스템 에러 ──

    def _draw_system_error(self, draw, message: str):
        bbox = draw.textbbox((0, 0), "ERROR", font=self._font_xl)
        tw = bbox[2] - bbox[0]
        draw.text(((DISPLAY_WIDTH - tw) // 2, 30), "ERROR", fill=COLOR_SYS_ERROR, font=self._font_xl)

        draw.rectangle([(8, 85), (232, 180)], outline=COLOR_SYS_ERROR, width=2)
        lines = self._wrap_text(draw, message, self._font, 208)
        y = 95
        for line in lines[:3]:
            draw.text((16, y), line, fill=COLOR_TEXT, font=self._font)
            y += 28

        draw.text((20, 200), "재시도 대기중...", fill=COLOR_DIM, font=self._font)

    # ── 버튼 ──

    def check_buttons(self) -> dict:
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
