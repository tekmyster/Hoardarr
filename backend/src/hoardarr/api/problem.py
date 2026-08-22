from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class Problem(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        self.errors = errors


def problem_response(request: Request, problem: Problem) -> JSONResponse:
    document: dict[str, Any] = {
        "type": f"https://hoardarr.dev/problems/{problem.code}",
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": request.url.path,
        "code": problem.code,
        "request_id": getattr(request.state, "request_id", None),
    }
    if problem.errors is not None:
        document["errors"] = problem.errors
    headers = {
        "X-Request-ID": str(getattr(request.state, "request_id", "")),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }
    headers.update(problem.headers)
    return JSONResponse(
        document,
        status_code=problem.status,
        headers=headers,
        media_type="application/problem+json",
    )


async def handle_problem(request: Request, exc: Problem) -> JSONResponse:
    return problem_response(request, exc)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Never echo rejected input: credentials may be present in authentication or
    # integration payloads.
    errors = [
        {
            "location": [str(part) for part in error.get("loc", ())],
            "message": error.get("msg", "Invalid value"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]
    return problem_response(
        request,
        Problem(
            422, "validation_failed", "Validation failed", "The request is invalid.", errors=errors
        ),
    )
