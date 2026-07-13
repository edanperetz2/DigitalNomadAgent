"""Strict request/response models for the 4 required API endpoints.

These field sets and nesting are copied verbatim from the course spec and
must never be renamed, re-nested, or extended with extra top-level fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StudentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str


class TeamInfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_batch_order_number: str
    team_name: str
    students: list[StudentInfo]


class PromptTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str


class LLMStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    prompt: dict
    response: dict


class PromptExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    full_response: str
    steps: list[LLMStep]


class AgentInfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    purpose: str
    prompt_template: PromptTemplate
    prompt_examples: list[PromptExample]


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=0)


class ExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    error: str | None
    response: str | None
    steps: list[LLMStep]
