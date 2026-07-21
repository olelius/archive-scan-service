"""FastAPI 应用入口和主进程生命周期。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import ApplicationContext
from app.api.devices import router as devices_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.pages import router as pages_router
from app.api.tasks import router as tasks_router
from app.api.dependencies import WorkerGatewayError
from app.errors import handle_stable_exception, register_exception_handlers
from app.services.page_service import PageRegistrationError
from app.services.task_service import TaskServiceError


def create_app(*, context: ApplicationContext | None = None) -> FastAPI:
    application_context = context or ApplicationContext()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        application_context.start()
        try:
            yield
        finally:
            application_context.close()

    app = FastAPI(
        title="档案本机扫描服务",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.context = application_context
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(application_context.settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.add_exception_handler(TaskServiceError, handle_stable_exception)
    app.add_exception_handler(PageRegistrationError, handle_stable_exception)
    app.add_exception_handler(WorkerGatewayError, handle_stable_exception)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(devices_router, prefix="/api/v1")
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(pages_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    return app


app = create_app()


__all__ = ["app", "create_app"]
