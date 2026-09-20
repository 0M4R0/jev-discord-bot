"""Thin async wrapper around typesafe-sdk for Jev System One evaluations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from typesafe_sdk import AsyncTypeSafeClient
from typesafe_sdk import Choice as SdkChoice
from typesafe_sdk import Noul as SdkNoul


@dataclass
class Choice:
    """Ask Jev to pick one option from a set of criteria."""

    instructions: str
    criteria: dict[str, str | None]


@dataclass
class Noul:
    """Ask Jev whether a statement is true (returns 0-1 probability)."""

    instructions: str


@dataclass
class QuestionResult:
    type: str
    choice: str | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = None
    noul: float | None = None


@dataclass
class TypeSafeEvaluationResponse:
    model: str
    answers: dict[str, QuestionResult]
    usage: dict[str, Any] | None = None


class AsyncTypeSafe:
    """Async client for TypeSafe System One (Jev)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "jev-latest",
        base_url: str = "https://api.typesafe.ai",
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def evaluate(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Choice | Noul],
        model: str | None = None,
    ) -> TypeSafeEvaluationResponse:
        target_model = model or self.model

        sdk_questions: dict[str, Any] = {}
        for key, q in questions.items():
            if isinstance(q, Choice):
                sdk_questions[key] = SdkChoice(
                    instructions=q.instructions,
                    criteria=q.criteria,
                )
            elif isinstance(q, Noul):
                sdk_questions[key] = SdkNoul(instructions=q.instructions)
            else:
                raise TypeError(f"Unsupported question type: {type(q)}")

        async with AsyncTypeSafeClient(
            api_key=self.api_key or None,
            model=target_model,
            base_url=self.base_url,
            timeout=self.timeout,
        ) as client:
            resp = await client.system_one(
                state=state,
                questions=sdk_questions,
                model=target_model,
            )

        answers: dict[str, QuestionResult] = {}
        raw_answers = getattr(resp, "answers", {}) or {}
        for key, ans in raw_answers.items():
            ans_type = getattr(ans, "type", None) or type(ans).__name__.lower()
            if hasattr(ans, "choice"):
                answers[key] = QuestionResult(
                    type="choice",
                    choice=ans.choice,
                    probabilities=getattr(ans, "probabilities", None),
                    confidence=getattr(ans, "confidence", None),
                )
            elif hasattr(ans, "noul"):
                answers[key] = QuestionResult(
                    type="noul",
                    noul=ans.noul,
                    confidence=getattr(ans, "confidence", None),
                )
            else:
                answers[key] = QuestionResult(type=str(ans_type))

        usage = getattr(resp, "usage", None)
        if usage is not None and not isinstance(usage, dict):
            usage = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            }

        return TypeSafeEvaluationResponse(
            model=getattr(resp, "model", target_model),
            answers=answers,
            usage=usage,
        )
