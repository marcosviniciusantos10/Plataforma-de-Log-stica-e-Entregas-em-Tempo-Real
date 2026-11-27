from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis, get_session
from app.schemas.auth import LoginIn, RefreshIn, RegisterIn, TokenOut, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _invalid_refresh() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido")


def _token_out(pair: auth_service.TokenPair) -> TokenOut:
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, session: AsyncSession = Depends(get_session)) -> UserOut:
    try:
        user = await auth_service.register(session, data)
    except auth_service.EmailAlreadyRegisteredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado"
        ) from None
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenOut)
async def login(
    data: LoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> TokenOut:
    ip = request.client.host if request.client else "unknown"
    try:
        pair = await auth_service.login(session, redis, data.email, data.password, ip)
    except auth_service.TooManyAttemptsError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente mais tarde.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except auth_service.InvalidCredentialsError:
        raise _invalid_credentials() from None
    return _token_out(pair)


@router.post("/refresh", response_model=TokenOut)
async def refresh(
    data: RefreshIn,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> TokenOut:
    try:
        pair = await auth_service.refresh(session, redis, data.refresh_token)
    except auth_service.InvalidRefreshTokenError:
        raise _invalid_refresh() from None
    return _token_out(pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshIn, redis: Redis = Depends(get_redis)) -> Response:
    await auth_service.logout(redis, data.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
