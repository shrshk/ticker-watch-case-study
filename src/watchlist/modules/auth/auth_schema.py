"""Request and response shapes for the auth domain."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(LoginRequest):
    email: str | None = None
    first_name: str = ""
    last_name: str = ""


class User(BaseModel):
    id: int
    username: str
    email: str | None = None
    first_name: str = ""
    last_name: str = ""


class Session(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: User
