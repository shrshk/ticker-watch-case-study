"""Login, registration and identity. Transport only."""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from watchlist.api.deps import connection, current_user
from watchlist.modules.auth import auth_handler
from watchlist.modules.auth.auth_schema import LoginRequest, RegisterRequest, Session, User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=Session, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    conn: asyncpg.Connection = Depends(connection),
) -> Session:
    try:
        return await auth_handler.register(
            conn,
            username=body.username,
            password=body.password,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
        )
    except auth_handler.UsernameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "username already taken") from exc


@router.post("/login", response_model=Session)
async def login(
    body: LoginRequest,
    conn: asyncpg.Connection = Depends(connection),
) -> Session:
    try:
        return await auth_handler.login(conn, body.username, body.password)
    except auth_handler.InvalidCredentialsError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password") from exc


@router.get("/me", response_model=User)
async def me(user: User = Depends(current_user)) -> User:
    return user
