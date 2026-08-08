from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.application.folders import FolderDomainError
from app.application.content_bank import ApplicationError, ConflictError, GoneError, IssuesError, NotFoundError
from app.application.managed_tags import TagError
from app.application.assessments import AssessmentError
from app.config import get_settings
from app.presentation.routes import router
from app.presentation.assessment_routes import router as assessment_router


app = FastAPI()
settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()], allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization"])
app.include_router(router)
app.include_router(assessment_router)

@app.exception_handler(AssessmentError)
async def assessment_error(_: Request, exc: AssessmentError) -> JSONResponse:
    return error_response(exc.code, str(exc), exc.details, exc.status)

@app.exception_handler(TagError)
async def tag_error(_: Request, exc: TagError) -> JSONResponse:
    details=[{"field":exc.field,"code":exc.code,"message":str(exc)}] if exc.field else []
    return error_response(exc.code,str(exc),details,exc.status)


def error_response(code: str, message: str, details: list[dict[str, str]], status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details, "request_id": str(uuid4())}})



@app.exception_handler(FolderDomainError)
async def folder_domain_error(_: Request, exc: FolderDomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error":{"code":exc.code,"message":str(exc),"details":exc.details,"request_id":str(uuid4())}})

@app.exception_handler(ApplicationError)
async def application_error(_: Request, exc: ApplicationError) -> JSONResponse:
    return error_response("validation_error", exc.message, [detail.__dict__ for detail in exc.details], 422)


@app.exception_handler(IssuesError)
async def issues_error(_: Request, exc: IssuesError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {"code": exc.code, "message": str(exc),
        "details": {"issues": [issue.__dict__ for issue in exc.issues]}, "request_id": str(uuid4())}})


@app.exception_handler(NotFoundError)
async def not_found_error(_: Request, exc: NotFoundError) -> JSONResponse:
    return error_response(exc.code, str(exc), [], 404)

@app.exception_handler(ConflictError)
async def conflict_error(_: Request, exc: ConflictError) -> JSONResponse:
    return error_response(exc.code, str(exc), [], 409)

@app.exception_handler(GoneError)
async def gone_error(_: Request, exc: GoneError) -> JSONResponse:
    return error_response(exc.code, str(exc), [], 410)


@app.exception_handler(RequestValidationError)
async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"field": ".".join(str(x) for x in error["loc"] if x != "body"), "code": error["type"], "message": error["msg"]} for error in exc.errors()]
    return error_response("validation_error", "Запрос содержит ошибки.", details, 422)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "validation_error"
    return error_response(code, str(exc.detail), [], exc.status_code)


@app.exception_handler(Exception)
async def internal_error(_: Request, exc: Exception) -> JSONResponse:
    return error_response("internal_error", "Внутренняя ошибка сервера.", [], 500)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
