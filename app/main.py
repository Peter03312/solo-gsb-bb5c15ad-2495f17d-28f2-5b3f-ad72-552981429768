"""FastAPI 应用：双盲论文评审分配服务。

鉴权：
  - 会务方接口需携带请求头  X-Organizer-Key: <ORGANIZER_KEY>
  - 评审人接口需在 Basic Auth 中提供 (评审人编号, 凭据)
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from . import database as db, repository as repo, service
from .config import settings
from .models import (DomainError, DryRunResult, PaperIn, PublishRequest,
                     PublishResult, ReviewerIn)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="双盲论文评审分配服务",
    version="1.0.0",
    description="论文/评审人资料维护、约束预演、带修订号的发布与评审人取稿。",
    lifespan=lifespan,
)


# ----------------------------- 错误处理 -----------------------------

@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message,
                           "details": exc.details}},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": {"code": "validation_error",
                           "message": "请求参数不合法（如容量须为非负整数、编号不得重复或为空）",
                           "details": exc.errors()}},
    )


# ----------------------------- 鉴权依赖 -----------------------------

def require_organizer(x_organizer_key: str = Header(default="")) -> None:
    if not x_organizer_key or not secrets.compare_digest(
            x_organizer_key, settings.organizer_key):
        raise DomainError("invalid_organizer_key",
                          "会务方密钥缺失或无效（请求头 X-Organizer-Key）",
                          status.HTTP_401_UNAUTHORIZED)


# ----------------------------- 健康检查/资料读取 -----------------------------

@app.get("/health", tags=["meta"])
def health() -> dict:
    with db.get_conn() as conn:
        rev = db.current_revision(conn)
    return {"status": "ok", "material_rev": rev}


@app.get("/material", tags=["organizer"],
         dependencies=[Depends(require_organizer)])
def get_material() -> dict:
    with db.get_conn() as conn:
        papers = [{k: r[k] for k in ("paper_id", "topic", "institution")}
                  for r in repo.list_papers(conn)]
        reviewers = [{
            "reviewer_id": r["reviewer_id"],
            "topics": repo._split(r["topics"]),
            "institution": r["institution"],
            "capacity": r["capacity"],
            "excluded_papers": repo._split(r["excluded"]),
            # 凭据哈希仅用于让会务方确认已设置，绝不返回明文
            "credential_set": True,
        } for r in repo.list_reviewers(conn)]
        return {"material_rev": db.current_revision(conn),
                "papers": papers, "reviewers": reviewers}


# ----------------------------- 论文维护 -----------------------------

@app.post("/papers", tags=["organizer"], status_code=201,
          dependencies=[Depends(require_organizer)])
def create_paper(payload: PaperIn) -> dict:
    rev = repo.create_paper(payload)
    return {"created": payload.paper_id, "material_rev": rev}


@app.put("/papers/{paper_id}", tags=["organizer"],
         dependencies=[Depends(require_organizer)])
def update_paper(paper_id: str, payload: PaperIn) -> dict:
    rev = repo.update_paper(paper_id, payload)
    return {"updated": paper_id, "material_rev": rev}


@app.delete("/papers/{paper_id}", tags=["organizer"],
            dependencies=[Depends(require_organizer)])
def delete_paper(paper_id: str) -> dict:
    rev = repo.delete_paper(paper_id)
    return {"deleted": paper_id, "material_rev": rev}


# ----------------------------- 评审人维护 -----------------------------

@app.post("/reviewers", tags=["organizer"], status_code=201,
          dependencies=[Depends(require_organizer)])
def create_reviewer(payload: ReviewerIn) -> dict:
    rev = repo.create_reviewer(payload)
    return {"created": payload.reviewer_id, "material_rev": rev}


@app.put("/reviewers/{reviewer_id}", tags=["organizer"],
         dependencies=[Depends(require_organizer)])
def update_reviewer(reviewer_id: str, payload: ReviewerIn) -> dict:
    rev = repo.update_reviewer(reviewer_id, payload)
    return {"updated": reviewer_id, "material_rev": rev}


@app.delete("/reviewers/{reviewer_id}", tags=["organizer"],
            dependencies=[Depends(require_organizer)])
def delete_reviewer(reviewer_id: str) -> dict:
    rev = repo.delete_reviewer(reviewer_id)
    return {"deleted": reviewer_id, "material_rev": rev}


# ----------------------------- 预演 / 发布 / 查询 -----------------------------

@app.post("/assignments/dry-run", response_model=DryRunResult,
          tags=["organizer"], dependencies=[Depends(require_organizer)])
def dry_run() -> DryRunResult:
    return service.dry_run()


@app.post("/assignments/publish", response_model=PublishResult,
          tags=["organizer"], dependencies=[Depends(require_organizer)])
def publish(payload: PublishRequest) -> PublishResult:
    return service.publish(payload.material_rev)


@app.get("/assignments", tags=["organizer"],
         dependencies=[Depends(require_organizer)])
def get_assignments() -> dict:
    return service.published_snapshot()


# ----------------------------- 评审人取稿 -----------------------------

@app.get("/reviewer/me/papers", tags=["reviewer"])
def reviewer_papers(request: Request) -> dict:
    import base64
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        raise DomainError("invalid_credential",
                          "需要 Basic Auth：用户名=评审人编号，密码=凭据", 401)
    try:
        raw = base64.b64decode(auth[6:]).decode()
        reviewer_id, credential = raw.split(":", 1)
    except Exception:
        raise DomainError("invalid_credential", "Basic Auth 格式无效", 401)
    return service.reviewer_view(reviewer_id, credential)
