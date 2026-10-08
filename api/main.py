import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from starlette.responses import Response

from api.config import get_settings
from api.logging_config import configure_logging
from api.repositories.postgres_repository import PostgresDB
from api.routers.journal_router import router as journal_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    settings = get_settings()
    async with PostgresDB(settings.database_url) as database:
        app.state.database = database
        try:
            # TODO (Task 3): Log API readiness at INFO after the database is ready.
            logger.info("API is ready")
            yield
        finally:
            # TODO (Task 3): Log API shutdown at INFO during cleanup.
            logger.info("API is shutting down")
            del app.state.database


app = FastAPI(
    title="Journal API",
    description="A simple journal API for tracking daily work, struggles, and intentions",
    lifespan=lifespan,
)
app.include_router(journal_router)


@app.middleware("http")
async def add_strict_transport_security(request: Request, call_next) -> Response:
    """Tell browsers to keep using HTTPS for this host."""
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/version")
def read_version() -> dict[str, str]:
    """Return the commit that this running process was deployed from."""
    return {"commit": os.environ.get("COMMIT_SHA", "unknown")}


# Configure OpenTelemetry tracing
resource = Resource.create(
    {
        "service.name": "journal-api",
        "deployment.environment.name": "local",
    }
)

provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint="http://localhost:4317",
            insecure=True,
        )
    )
)
trace.set_tracer_provider(provider)
AsyncPGInstrumentor().instrument()

FastAPIInstrumentor.instrument_app(app)
