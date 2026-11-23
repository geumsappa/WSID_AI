"""피해 대응 이미지 OCR. 원문은 호출자에게만 반환하고 모델에는 가려서 보낸다."""
import io
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from PIL import Image, UnidentifiedImageError

from app.api_errors import AiApiError
from app.config import settings
from app.guard.scenario_guard import PHONE_PATTERN
from app.guard.sensitive import NUMERIC_INFO_WORDS, mask_requested_numbers, mask_sensitive
from app.schemas.internal import OcrResult
from rag.openai_client import generate_json
from rag.guard import has_forbidden

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_TEXT_LENGTH = 1000  # Spring /extract description 상한과 같음. 넘으면 잘라서 전달
OCR_TIMEOUT_SECONDS = 15
# 예산: OCR 15초(대기 포함) + 분류 대기 1초 + 분류 10초 = 최대 26초 < Spring OCR 응답 제한 30초
CLASSIFY_TIMEOUT_SECONDS = settings.ocr_classify_timeout_seconds
CLASSIFY_QUEUE_WAIT_SECONDS = 1
OCR_QUEUE_WAIT_SECONDS = settings.ocr_queue_wait_seconds
logger = logging.getLogger(__name__)
IMAGE_TYPES = {"SMS", "MESSENGER_CHAT", "TRANSFER_RECEIPT", "WEB_PAGE", "OTHER"}
_URL = re.compile(r"(?i)(?:https?://|www\.)[^\s<>]+|\b[\w-]+\.(?:com|net|org|kr|co|io|me|ly)(?:/[^\s<>]*)?")
# 요약은 사용자에게 "사진을 확인했어요" 말풍선으로 보여요. 상황별 해석 규칙(예: 계좌번호=송금 요구)을 넣지 않고
# 사진에 보이는 행동만 쓰게 해요. 해석·조언은 사실 추출과 상담 답변 단계가 맡아요.
_SYSTEM = ("사진 속 텍스트의 종류를 SMS, MESSENGER_CHAT, TRANSFER_RECEIPT, WEB_PAGE, OTHER 중 하나로 선택하고, "
           "두 문장 이내로 요약하세요. 요약에는 사진에 보이는 사실만 쓰세요: "
           "상대가 자신을 누구라고 했는지, 상대가 보낸 것(계좌번호, 링크, 파일, 사진 등)과 요구한 것, "
           "사용자가 이미 한 행동(송금, 정보 입력 등), 사진 속에 드러난 위험 신호. "
           "사진에 없는 항목은 생략하고, 없다고 쓰지 마세요. 사진에 없는 의도나 사실을 추측하지 마세요. "
           "상대의 신원은 '판매자라며'처럼 상대가 한 말로 쓰세요. "
           "조언, 해야 할 일, 대화 분위기나 감정은 쓰지 마세요. "
           "연락처, 링크 주소, 계좌번호, 이름 및 기타 개인정보는 쓰지 마세요. "
           "'~예요', '~해요' 말투로 쓰세요. imageType과 summary를 가진 JSON만 반환하세요.")
_paddle = None
_ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paddle-ocr")
_classify_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr-classify")
_ocr_slot = threading.BoundedSemaphore(1)
_classify_slot = threading.BoundedSemaphore(1)


def initialize_ocr() -> None:
    """각 서버 프로세스 시작 시 한 번 모델을 로드한다.
    실패해도 예외를 올리지 않는다: 서버(훈련·피해 대응 API)는 정상 기동하고 OCR 요청만 503을 반환한다."""
    global _paddle
    if settings.ocr_engine == "tesseract":
        return
    if settings.ocr_engine != "paddle":
        logger.error("OCR 비활성화: OCR_ENGINE은 paddle 또는 tesseract여야 합니다.")
        return
    if _paddle is not None:
        return
    try:
        from paddleocr import PaddleOCR
        _paddle = PaddleOCR(
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False, use_doc_unwarping=False,
            use_textline_orientation=False, device="cpu", enable_mkldnn=False,
        )
    except Exception as error:  # noqa: BLE001  설치 누락·모델 다운로드 실패 등
        # 예외 메시지에 경로 등이 들어갈 수 있어 종류만 남긴다.
        logger.error("OCR 비활성화: PaddleOCR 로드 실패 (%s). OCR 요청은 503을 반환합니다.", type(error).__name__)


def _validate_image(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as image:
            if image.width * image.height > MAX_PIXELS:
                raise AiApiError(413, "FILE_TOO_LARGE", "이미지 해상도를 낮춰 다시 올려 주세요.")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise AiApiError(422, "INVALID_REQUEST", "PNG 또는 JPEG 이미지를 올려 주세요.") from None


def _ordered_text(texts, boxes) -> str:
    """같은 높이의 상자는 같은 줄로 묶고 왼쪽부터 읽는다."""
    entries = []
    for text, box in zip(texts, boxes):
        if not str(text).strip():
            continue
        x1, y1, x2, y2 = map(float, box)
        entries.append((y1, x1, y2, str(text).strip()))
    entries.sort()
    lines = []
    for y1, x1, y2, text in entries:
        if not lines or abs(y1 - lines[-1][0]) > max(3, (y2 - y1) * 0.5):
            lines.append([y1, [(x1, text)]])
        else:
            lines[-1][1].append((x1, text))
    return "\n".join(" ".join(text for _, text in sorted(items)) for _, items in lines)


def _paddle_recognize(data: bytes) -> str:
    import numpy as np
    with Image.open(io.BytesIO(data)) as image:
        bgr = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
    results = _paddle.predict(bgr)
    parts = []
    for result in results:
        value = result.json["res"]
        parts.append(_ordered_text(value.get("rec_texts", []), value.get("rec_boxes", [])))
    return "\n".join(part for part in parts if part)


def _submit_bounded(pool, slot, function, *args, wait: float = 0):
    """작업 슬롯이 비면 실행을 맡긴다. 사용 중이면 최대 wait초 기다리고, 그래도 안 비면 None."""
    if not slot.acquire(timeout=max(wait, 0)):
        return None
    try:
        future = pool.submit(function, *args)
    except BaseException:
        slot.release()
        raise
    future.add_done_callback(lambda _: slot.release())
    return future


def _recognize(data: bytes, timeout: float) -> str:
    if settings.ocr_engine == "paddle":
        if _paddle is None:
            raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "OCR 실행 환경이 준비되지 않았어요.")
        started = time.monotonic()
        future = _submit_bounded(_ocr_pool, _ocr_slot, _paddle_recognize, data,
                                 wait=min(OCR_QUEUE_WAIT_SECONDS, timeout))
        if future is None:
            raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "OCR이 사용 중이에요. 잠시 후 다시 시도해 주세요.")
        try:
            # 대기한 시간도 같은 OCR 예산에서 뺀다.
            return future.result(timeout=max(timeout - (time.monotonic() - started), 0.01))
        except TimeoutError:
            raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "이미지 인식 시간이 초과됐어요.") from None
        except Exception:
            raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "OCR 실행에 실패했어요.") from None
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


def _mask(text: str) -> str:
    """민감정보, '인증번호' 같은 말 뒤의 숫자, 링크, 전화번호를 가린 글자."""
    masked, _ = mask_sensitive(text)
    requested = [word for word in NUMERIC_INFO_WORDS if word.lower() in text.lower()]
    masked, _ = mask_requested_numbers(masked, requested)
    masked = _URL.sub("[링크]", masked)
    return PHONE_PATTERN.sub("[전화번호]", masked)


def _classify(text: str) -> tuple[str, str | None]:
    masked = _mask(text)
    raw = generate_json(_SYSTEM, f"<사진 속 글자>\n{masked}\n</사진 속 글자>",
                        {"imageType": "OTHER", "summary": None},
                        model=settings.ocr_classify_model or None, timeout=CLASSIFY_TIMEOUT_SECONDS)
    if not isinstance(raw, dict):
        return "OTHER", None
    image_type = raw.get("imageType")
    if not isinstance(image_type, str) or image_type not in IMAGE_TYPES:
        image_type = "OTHER"
    summary = raw.get("summary")
    if not isinstance(summary, str):
        summary = None
    else:
        summary = summary.strip()[:300] or None
        if summary and (has_forbidden(summary, strict=True) or mask_sensitive(summary)[1]):
            summary = None
    return image_type, summary


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
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH].rstrip()
    image_type, summary = "OTHER", None
    future = _submit_bounded(_classify_pool, _classify_slot, _classify, text,
                             wait=CLASSIFY_QUEUE_WAIT_SECONDS)
    if future is not None:
        try:
            image_type, summary = future.result(timeout=CLASSIFY_TIMEOUT_SECONDS)
        except Exception:
            pass  # 텍스트는 유지한다. 실행 중인 호출은 끝날 때 슬롯을 반환한다.
    return OcrResult(recognizedText=text, usable=True, imageType=image_type, summary=summary,
                     maskedText=_mask(text))
