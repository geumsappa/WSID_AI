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
