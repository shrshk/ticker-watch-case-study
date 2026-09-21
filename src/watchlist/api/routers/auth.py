"""Login, registration and identity."""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from watchlist.api.deps import connection, current_user
from watchlist.api.schemas import LoginRequest, RegisterRequest, TokenOut, UserOut
from watchlist.shared.repo import users as user_repo
from watchlist.shared.security import hash_password, issue_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(row: asyncpg.Record) -> UserOut:
    return UserOut(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    conn: asyncpg.Connection = Depends(connection),
) -> TokenOut:
    try:
        row = await user_repo.create(
            conn,
            username=body.username,
            password_hash=hash_password(body.password),
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "username already taken") from exc

    token, ttl = issue_token(row["id"], row["username"])
    return TokenOut(access_token=token, expires_in=ttl, user=_user_out(row))


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginRequest,
    conn: asyncpg.Connection = Depends(connection),
) -> TokenOut:
    row = await user_repo.get_by_username(conn, body.username)
    # Same error either way: do not tell a caller which usernames exist.
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")

    token, ttl = issue_token(row["id"], row["username"])
    return TokenOut(access_token=token, expires_in=ttl, user=_user_out(row))


@router.get("/me", response_model=UserOut)
async def me(user: asyncpg.Record = Depends(current_user)) -> UserOut:
    return _user_out(user)
