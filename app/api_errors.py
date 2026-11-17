from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AiApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "success": False,
        "error": {"code": code, "message": message},
    })


async def handle_ai_error(_request: Request, error: AiApiError) -> JSONResponse:
    return error_response(error.status, error.code, error.message)


async def handle_validation(request: Request, error: RequestValidationError) -> JSONResponse:
    if request.url.path == "/internal/ai/ocr/extract":
        # Pydantic의 기본 오류 응답에 포함될 수 있는 원문 입력을 제거한다.
        return error_response(422, "INVALID_REQUEST", "요청 형식이 올바르지 않아요.")
    return await request_validation_exception_handler(request, error)
