from pydantic import BaseModel


class ApiMessage(BaseModel):
    message: str
