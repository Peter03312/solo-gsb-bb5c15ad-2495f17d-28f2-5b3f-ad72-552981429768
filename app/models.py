"""Pydantic 请求/响应模型与领域错误。"""
from __future__ import annotations

from typing import List, Optional

from fastapi import status
from pydantic import BaseModel, Field, field_validator


class DomainError(Exception):
    """业务错误，由统一异常处理器映射为明确的 HTTP 响应。"""

    def __init__(self, code: str, message: str, http_status: int = status.HTTP_400_BAD_REQUEST,
                 details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


class PaperIn(BaseModel):
    paper_id: str = Field(..., min_length=1, description="论文编号，全局唯一")
    anonymous_text: str = Field(..., min_length=1, description="匿名稿内容")
    topic: str = Field(..., min_length=1, description="主题")
    institution: str = Field(..., min_length=1, description="作者机构")

    @field_validator("paper_id", "topic", "institution")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不得为空白")
        return v


class ReviewerIn(BaseModel):
    reviewer_id: str = Field(..., min_length=1, description="评审人编号，全局唯一")
    credential: str = Field(..., min_length=1, description="评审人访问凭据（评审密钥）")
    topics: List[str] = Field(..., min_length=1, description="擅长主题列表")
    institution: str = Field(..., min_length=1, description="评审人所属机构")
    capacity: int = Field(..., ge=0, description="评审容量，必须为非负整数")
    excluded_papers: List[str] = Field(default_factory=list, description="回避论文编号列表")

    @field_validator("reviewer_id", "credential", "institution")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不得为空白")
        return v

    @field_validator("topics", "excluded_papers")
    @classmethod
    def _strip_list(cls, v: List[str]) -> List[str]:
        out = [x.strip() for x in v]
        if any(not x for x in out):
            raise ValueError("列表元素不得为空白")
        return out


class Assignment(BaseModel):
    paper_id: str
    reviewer_id: str
    expert: bool = Field(..., description="该评审人是否擅长本论文主题")


class UnassignedPaper(BaseModel):
    paper_id: str
    reasons: List[str] = Field(..., description="无法完整分配的限制原因")


class DryRunResult(BaseModel):
    material_rev: int
    complete: bool = Field(..., description="是否存在覆盖全部论文的完整方案")
    max_load_ratio: float = Field(..., description="方案中最高的 已用/容量 比例")
    assignments: List[Assignment]
    unassigned: List[UnassignedPaper]
    note: str = ""


class PublishRequest(BaseModel):
    material_rev: int = Field(..., description="预演时返回的资料修订号")


class PublishResult(BaseModel):
    published: bool
    material_rev: int
    assignments: List[Assignment]
    note: str = ""


class ReviewerPaper(BaseModel):
    paper_id: str
    topic: str
    anonymous_text: str
    expert: bool
    material_rev: int
