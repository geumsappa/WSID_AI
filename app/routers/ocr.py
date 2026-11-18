from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from app.api_errors import AiApiError
from app.dependencies import require_internal_key
from app.schemas.internal import OcrResult, Success
from app.services.ocr import MAX_IMAGE_BYTES, extract_text

router = APIRouter(prefix="/internal/ai/ocr", tags=["내부 AI · OCR"],
                   dependencies=[Depends(require_internal_key)])


@router.post("/extract", response_model=Success[OcrResult])
def recognize_images(images: Annotated[list[UploadFile], File()]):
    if len(images) > 3:
        raise AiApiError(413, "FILE_TOO_LARGE", "이미지는 최대 3장까지 올릴 수 있어요.")
    # 경로와 원래 파일명은 OCR 프로세스에 전달하지 않는다.
    return Success(data=extract_text([file.file.read(MAX_IMAGE_BYTES + 1) for file in images]))
