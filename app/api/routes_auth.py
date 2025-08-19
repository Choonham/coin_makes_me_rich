
# ===================================================================================
#   api/routes_auth.py: 인증 API 엔드포인트
# ===================================================================================
#
#   - 사용자 로그인 및 JWT(JSON Web Token) 발급을 처리합니다.
#   - API의 보호된 엔드포인트에 접근하기 위해 필요한 토큰을 생성합니다.
#
#   **주요 기능:**
#   - **로그인**: 사용자 이름과 비밀번호를 받아 인증을 수행합니다.
#   - **비밀번호 검증**: `passlib`를 사용하여 안전하게 해시된 비밀번호를 비교합니다.
#   - **JWT 생성**: 인증 성공 시, `python-jose`를 사용하여 JWT 액세스 토큰을 생성하여 반환합니다.
#
#   **보안 고려사항:**
#   - 실제 프로덕션 환경에서는 HTTPS(TLS)를 통해 모든 API 통신을 암호화해야 합니다.
#   - 관리자 계정 정보는 .env 파일을 통해 안전하게 관리되어야 합니다.
#   - JWT 시크릿 키는 복잡하고 예측 불가능한 문자열로 설정해야 합니다.
#
#
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.config import settings

router = APIRouter()

# --------------------------------------------------------------------------
# 보안 관련 설정 (Security Configuration)
# --------------------------------------------------------------------------

# 비밀번호 해싱을 위한 컨텍스트 설정 (bcrypt 알고리즘 사용)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 스킴 정의. React Native 앱은 여기서 받은 토큰을 이후 요청의
# Authorization 헤더에 `Bearer <token>` 형태로 포함해야 합니다.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# --------------------------------------------------------------------------
# Pydantic 모델 정의
# --------------------------------------------------------------------------

class Token(BaseModel):
    """JWT 토큰 응답 모델"""
    access_token: str
    token_type: str

class TokenData(BaseModel):
    """JWT 토큰에 담길 데이터 모델"""
    username: str | None = None

class LoginRequest(BaseModel):
    """로그인 요청 모델"""
    username: str
    password: str

# --------------------------------------------------------------------------
# 유틸리티 함수 (Utility Functions)
# --------------------------------------------------------------------------

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """일반 텍스트 비밀번호와 해시된 비밀번호를 비교합니다."""
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """JWT 액세스 토큰을 생성합니다."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        # 기본 만료 시간: 15분
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    """
    요청 헤더의 JWT 토큰을 디코딩하고 유효성을 검증하여 현재 사용자를 식별합니다.
    보호된 엔드포인트에서 의존성으로 사용됩니다.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM]
        )
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        # 여기서 TokenData 모델로 유효성 검사를 추가할 수도 있습니다.
        # token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    # 실제 애플리케이션에서는 데이터베이스에서 사용자 정보를 조회할 수 있습니다.
    # 이 예제에서는 사용자 이름이 관리자 이름과 일치하는지만 확인합니다.
    if username != settings.ADMIN_USER:
        raise credentials_exception

    return username

# --------------------------------------------------------------------------
# 인증 API 엔드포인트
# --------------------------------------------------------------------------

@router.post("/login", response_model=Token)
async def login_for_access_token(login_request: LoginRequest) -> Token:
    """
    ## 사용자 로그인 및 JWT 토큰 발급

    사용자 이름과 비밀번호를 `JSON`으로 받아 인증을 수행합니다.
    성공 시, API 접근에 필요한 `access_token`을 발급합니다.

    **요청 형식:** `application/json`
    ```json
    {
        "username": "admin",
        "password": "admin"
    }
    ```

    **RN 앱 연동 가이드:**
    - 로그인 폼에서 받은 아이디/비밀번호로 이 엔드포인트를 호출합니다.
    - 성공적으로 받은 `access_token`을 앱의 안전한 저장소(e.g., SecureStore)에 저장합니다.
    - 이후 모든 제어/주문 관련 API 요청 시 `Authorization: Bearer <token>` 헤더를 추가합니다.
    """
    # .env에 설정된 관리자 계정과 비교하여 인증
    is_valid_user = (login_request.username == settings.ADMIN_USER)
    is_valid_password = verify_password(login_request.password, settings.ADMIN_PASS_HASH)

    if not (is_valid_user and is_valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 토큰 만료 시간 설정
    access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    # 토큰 생성
    access_token = create_access_token(
        data={"sub": login_request.username}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}
