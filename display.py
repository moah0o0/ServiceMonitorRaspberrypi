"""
PiTFT 디스플레이 (240x240)
Pillow로 이미지 생성 → SPI로 전송
라즈베리파이가 아닌 환경에서는 자동 비활성화
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

        self._detail_mode = False
        self._system_error: str | None = None

    def set_system_error(self, message: str | None):
        """시스템 에러 설정 (None이면 해제)"""
        self._system_error = message

    def update(self, states: list[dict]):
        """화면 갱신"""
        if not self.enabled:
            return

        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), COLOR_BG)
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        except Exception:
            font = ImageFont.load_default()
            font_sm = font

        # 상단: 시간 + 요약
        now = datetime.now(KST).strftime("%m/%d %H:%M:%S")
        ok_count = sum(1 for s in states if s["status"] == "ok")
        total = len(states)
        summary_color = COLOR_OK if ok_count == total else COLOR_ERROR

        draw.text((5, 5), now, fill=COLOR_TEXT, font=font)

        # 시스템 에러가 있으면 전체 화면에 경고 표시
        if self._system_error:
            draw.text((140, 5), "SYSTEM", fill=COLOR_SYS_ERROR, font=font)
            draw.line([(0, 22), (240, 22)], fill=COLOR_SYS_ERROR, width=1)
            self._draw_system_error(draw, self._system_error, font, font_sm)
            self.disp.image(img)
            return

        draw.text((140, 5), f"{ok_count}/{total} OK", fill=summary_color, font=font)
        draw.line([(0, 22), (240, 22)], fill=COLOR_DIM, width=1)

        # 중단: 서비스 상태 그리드
        if self._detail_mode:
            self._draw_detail(draw, states, font_sm)
        else:
            self._draw_grid(draw, states, font_sm)

        # 디스플레이 전송
        self.disp.image(img)

    def _draw_system_error(self, draw, message: str, font, font_sm):
        """시스템 에러 전체 화면 표시"""
        # 큰 경고 아이콘 영역
        draw.rectangle([(10, 40), (230, 140)], outline=COLOR_SYS_ERROR, width=2)
        try:
            font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        except Exception:
            font_lg = font

        draw.text((60, 55), "! ERROR !", fill=COLOR_SYS_ERROR, font=font_lg)
        # 에러 메시지 (줄바꿈 처리)
        lines = []
        while message:
            lines.append(message[:26])
            message = message[26:]
        y = 90
        for line in lines[:3]:
            draw.text((20, y), line, fill=COLOR_TEXT, font=font)
            y += 14

        # 하단: 재시도 안내
        draw.text((30, 170), "자동 재시도 대기 중...", fill=COLOR_DIM, font=font_sm)

    def _draw_grid(self, draw, states: list[dict], font):
        """그리드 모드: 서비스명 + 상태 도트"""
        y = 28
        for s in states:
            color = COLOR_OK if s["status"] == "ok" else COLOR_ERROR
            # 상태 도트
            draw.ellipse([(5, y + 2), (13, y + 10)], fill=color)
            # 서비스명 (잘라서 표시)
            name = s["name"][:22]
            draw.text((18, y), name, fill=COLOR_TEXT, font=font)
            y += 14
            if y > 235:
                break

    def _draw_detail(self, draw, states: list[dict], font):
        """상세 모드: 에러 서비스만 메시지 포함"""
        y = 28
        errors = [s for s in states if s["status"] != "ok"]
        if not errors:
            draw.text((5, y), "모든 서비스 정상", fill=COLOR_OK, font=font)
            return

        for s in errors:
            draw.ellipse([(5, y + 2), (13, y + 10)], fill=COLOR_ERROR)
            draw.text((18, y), s["name"][:22], fill=COLOR_TEXT, font=font)
            y += 14
            msg = s.get("message", "")[:30]
            draw.text((18, y), msg, fill=COLOR_WARN, font=font)
            y += 14
            if y > 230:
                break

    def check_buttons(self) -> dict:
        """버튼 상태 확인. 반환: {"detail_toggle": bool, "force_check": bool}"""
        result = {"detail_toggle": False, "force_check": False}
        if not self.buttons_enabled:
            return result
        # Mini PiTFT 버튼은 눌리면 False (active low)
        if not self.button_a.value:
            self._detail_mode = not self._detail_mode
            result["detail_toggle"] = True
        if not self.button_b.value:
            result["force_check"] = True
        return result
