import io
import subprocess
import time

from PIL import Image, UnidentifiedImageError

from app.api_errors import AiApiError
from app.config import settings
from app.guard.scenario_guard import PHONE_PATTERN, URL_PATTERN
from app.guard.sensitive import mask_sensitive
from app.schemas.internal import OcrResult

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_TEXT_LENGTH = 1000
OCR_TIMEOUT_SECONDS = 15


def _validate_image(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as image:
            if image.width * image.height > MAX_PIXELS:
                raise AiApiError(413, "FILE_TOO_LARGE", "이미지 해상도를 낮춰 다시 올려 주세요.")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise AiApiError(422, "INVALID_REQUEST", "PNG 또는 JPEG 이미지를 올려 주세요.") from None


def _recognize(data: bytes, timeout: float) -> str:
    try:
        result = subprocess.run(
            [settings.ocr_executable, "stdin", "stdout", "-l", settings.ocr_languages],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "이미지 인식 시간이 초과됐어요.") from None
    except OSError:
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "OCR 실행 환경이 준비되지 않았어요.") from None
    if result.returncode:
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "OCR 실행에 실패했어요. 언어 설정을 확인해 주세요.")
    return result.stdout.decode("utf-8", errors="replace").strip()


def extract_text(images: list[bytes]) -> OcrResult:
    if not images:
        raise AiApiError(422, "INVALID_REQUEST", "이미지를 한 장 이상 올려 주세요.")
    if len(images) > 3 or any(len(image) > MAX_IMAGE_BYTES for image in images):
        raise AiApiError(413, "FILE_TOO_LARGE", "이미지는 최대 3장, 장당 5MB까지 올릴 수 있어요.")
    for image in images:
        _validate_image(image)
    deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
    parts = []
    for image in images:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "이미지 인식 시간이 초과됐어요.")
        parts.append(_recognize(image, remaining))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise AiApiError(422, "OCR_FAILED", "이미지에서 문자를 읽지 못했어요.")
    _, sensitive = mask_sensitive(text)
    if sensitive or URL_PATTERN.search(text) or PHONE_PATTERN.search(text) or len(text) > MAX_TEXT_LENGTH:
        return OcrResult(recognizedText="", usable=False)
    return OcrResult(recognizedText=text, usable=True)
