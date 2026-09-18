"""Thin async wrapper around typesafe-sdk for Jev System One evaluations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from typesafe_sdk import AsyncTypeSafeClient, Choice as SdkChoice, Noul as SdkNoul


@dataclass
class Choice:
    """Ask Jev to pick one option from a set of criteria."""

    instructions: str
    criteria: Dict[str, Optional[str]]


@dataclass
class Noul:
    """Ask Jev whether a statement is true (returns 0-1 probability)."""

    instructions: str


@dataclass
class QuestionResult:
    type: str
    choice: Optional[str] = None
    probabilities: Optional[Dict[str, float]] = None
    confidence: Optional[float] = None
    noul: Optional[float] = None


@dataclass
class TypeSafeEvaluationResponse:
    model: str
    answers: Dict[str, QuestionResult]
    usage: Optional[Dict[str, Any]] = None


class AsyncTypeSafe:
    """Async client for TypeSafe System One (Jev)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
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
        state: Union[str, Dict[str, Any], List[Any]],
        questions: Dict[str, Union[Choice, Noul]],
        model: Optional[str] = None,
    ) -> TypeSafeEvaluationResponse:
        target_model = model or self.model

        sdk_questions: Dict[str, Any] = {}
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
            default_model=target_model,
            base_url=self.base_url,
            timeout=self.timeout,
        ) as client:
            resp = await client.system_one(
                state=state,
                questions=sdk_questions,
                model=target_model,
            )

        answers: Dict[str, QuestionResult] = {}
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
