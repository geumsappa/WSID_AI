from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

Data = TypeVar("Data")


class Success(BaseModel, Generic[Data]):
    success: Literal[True] = True
    data: Data


class OcrResult(BaseModel):
    recognizedText: str
    usable: bool
    imageType: Literal["SMS", "MESSENGER_CHAT", "TRANSFER_RECEIPT", "WEB_PAGE", "OTHER"]
    summary: str | None
    # 사진 요약 LLM에 보낸 것과 같은 가린 글자(민감정보·인증번호 뒤 숫자·링크·전화번호).
    # Spring이 상담 대화에 사진 내용을 남겨 이후 질문의 history로 쓸 때 원문 대신 이걸 저장해요.
    maskedText: str | None = None
