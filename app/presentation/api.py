"""FastAPI app: HTTP routes plus static file serving for the frontend."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.application.github import GithubActivityService, GithubFeedService
from app.application.links import CommandService, ImageService, LinkService
from app.application.logs import LogFilterService, LogQueryService
from app.application.settings import SettingsService
from app.domain.errors import ExternalServiceError, NotFoundError, ValidationError
from app.domain.github import EventSubject, FeedSpec
from app.domain.logs import LogQuery
from app.presentation import schemas


class Services:
    """Dependency container assembled by app.bootstrap."""

    def __init__(
        self,
        links: LinkService,
        commands: CommandService,
        filters: LogFilterService,
        logs: LogQueryService,
        settings: SettingsService,
        images: ImageService,
        github_feeds: GithubFeedService,
        github: GithubActivityService,
    ):
        self.links = links
        self.commands = commands
        self.filters = filters
        self.logs = logs
        self.settings = settings
        self.images = images
        self.github_feeds = github_feeds
        self.github = github


def create_app(services: Services, web_dir: Path, images_dir: Path) -> FastAPI:
    app = FastAPI(title="OpsDeck", docs_url="/api/docs")

    @app.middleware("http")
    async def no_heuristic_caching(request: Request, call_next):
        # Without Cache-Control, browsers cache static files heuristically and
        # may keep stale JS/CSS after a deploy. no-cache forces revalidation
        # (ETag 304s are instant on localhost).
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, error: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ValidationError)
    async def invalid(request: Request, error: ValidationError):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception):
        # last resort: a readable reason instead of a bare "Internal Server Error"
        logging.getLogger("opsdeck").exception("unhandled error on %s", request.url.path)
        detail = str(error).strip().splitlines()[0] if str(error).strip() else ""
        message = f"Unexpected server error ({type(error).__name__})"
        return JSONResponse(
            status_code=500, content={"detail": f"{message}: {detail}" if detail else message}
        )

    @app.exception_handler(ExternalServiceError)
    async def backend_failed(request: Request, error: ExternalServiceError):
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @app.get("/api/health", response_model=schemas.HealthOut)
    def health():
        return schemas.HealthOut(
            logs=services.logs.provider_available(),
            github=services.github.provider_available(),
        )

    # ----- Links -------------------------------------------------------

    @app.get("/api/links", response_model=list[schemas.LinkOut])
    def list_links():
        return [schemas.LinkOut.model_validate(link) for link in services.links.list_links()]

    @app.post("/api/links", response_model=schemas.LinkOut, status_code=201)
    def create_link(body: schemas.LinkIn):
        return schemas.LinkOut.model_validate(services.links.create_link(**body.model_dump()))

    # declared before the parameterized route so "order" is not read as an id
    @app.put("/api/links/order", status_code=204)
    def reorder_links(body: schemas.ReorderIn):
        services.links.reorder_links(body.ids)

    @app.put("/api/links/{link_id}", response_model=schemas.LinkOut)
    def update_link(link_id: str, body: schemas.LinkIn):
        link = services.links.update_link(link_id, **body.model_dump())
        return schemas.LinkOut.model_validate(link)

    @app.post("/api/links/{link_id}/run", response_model=schemas.CommandResultOut)
    def run_link_command(link_id: str):
        return schemas.CommandResultOut(message=services.commands.run_link(link_id))

    @app.delete("/api/links/{link_id}", status_code=204)
    def delete_link(link_id: str):
        services.links.delete_link(link_id)

    # ----- Saved log filters -------------------------------------------

    @app.get("/api/filters", response_model=list[schemas.LogFilterOut])
    def list_filters():
        return [schemas.LogFilterOut.model_validate(item) for item in services.filters.list_filters()]

    @app.post("/api/filters", response_model=schemas.LogFilterOut, status_code=201)
    def create_filter(body: schemas.LogFilterIn):
        created = services.filters.create_filter(**body.model_dump())
        return schemas.LogFilterOut.model_validate(created)

    @app.put("/api/filters/order", status_code=204)
    def reorder_filters(body: schemas.ReorderIn):
        services.filters.reorder_filters(body.ids)

    @app.put("/api/filters/{filter_id}", response_model=schemas.LogFilterOut)
    def update_filter(filter_id: str, body: schemas.LogFilterIn):
        updated = services.filters.update_filter(filter_id, **body.model_dump())
        return schemas.LogFilterOut.model_validate(updated)

    @app.delete("/api/filters/{filter_id}", status_code=204)
    def delete_filter(filter_id: str):
        services.filters.delete_filter(filter_id)

    # ----- Log queries --------------------------------------------------

    def _log_result(command: str, entries) -> schemas.LogQueryOut:
        return schemas.LogQueryOut(
            command=command,
            entries=[schemas.LogEntryOut.model_validate(entry) for entry in entries],
        )

    @app.post("/api/filters/{filter_id}/query", response_model=schemas.LogQueryOut)
    def run_saved_filter(filter_id: str):
        return _log_result(*services.logs.run_saved_filter(filter_id))

    @app.post("/api/logs/query", response_model=schemas.LogQueryOut)
    def query_logs(body: schemas.LogQueryIn):
        query = LogQuery(**body.model_dump())
        return _log_result(*services.logs.run_query(query))

    # ----- GitHub feeds -------------------------------------------------

    def _spec(body: schemas.GithubQueryIn) -> FeedSpec:
        return FeedSpec(
            org=body.org,
            repos=tuple(body.repos),
            event_kinds=tuple(body.event_kinds),
            hide_own=body.hide_own,
            hide_bots=body.hide_bots,
        )

    @app.get("/api/github/feeds", response_model=list[schemas.GithubFeedOut])
    def list_github_feeds():
        return [schemas.GithubFeedOut.model_validate(feed) for feed in services.github_feeds.list_feeds()]

    @app.post("/api/github/feeds", response_model=schemas.GithubFeedOut, status_code=201)
    def create_github_feed(body: schemas.GithubFeedIn):
        created = services.github_feeds.create_feed(**body.model_dump())
        return schemas.GithubFeedOut.model_validate(created)

    # fixed paths declared before /{feed_id} so they are not read as ids
    @app.put("/api/github/feeds/order", status_code=204)
    def reorder_github_feeds(body: schemas.ReorderIn):
        services.github_feeds.reorder_feeds(body.ids)

    @app.get("/api/github/feeds/unseen", response_model=dict[str, int])
    def github_unseen_counts():
        return services.github.unseen_counts()

    @app.get("/api/github/token", response_model=schemas.GithubTokenOut)
    def github_token_status():
        """Checks the token against GitHub and says what is missing, if anything."""
        return schemas.GithubTokenOut.model_validate(services.github.token_status())

    @app.post("/api/github/feeds/preview", response_model=schemas.FeedSpecOut)
    def preview_github_feed(body: schemas.GithubQueryIn):
        """How the server reads a feed: canonical repo names and kinds, or 422."""
        return schemas.FeedSpecOut.model_validate(_spec(body))

    @app.put("/api/github/feeds/{feed_id}", response_model=schemas.GithubFeedOut)
    def update_github_feed(feed_id: str, body: schemas.GithubFeedIn):
        updated = services.github_feeds.update_feed(feed_id, **body.model_dump())
        return schemas.GithubFeedOut.model_validate(updated)

    @app.delete("/api/github/feeds/{feed_id}", status_code=204)
    def delete_github_feed(feed_id: str):
        services.github_feeds.delete_feed(feed_id)

    @app.post("/api/github/feeds/{feed_id}/seen", response_model=schemas.GithubFeedOut)
    def mark_github_feed_seen(feed_id: str, body: schemas.GithubSeenIn):
        feed = services.github_feeds.mark_seen(feed_id, body.seen_at)
        return schemas.GithubFeedOut.model_validate(feed)

    @app.post("/api/github/feeds/{feed_id}/query", response_model=schemas.GithubActivityOut)
    def run_saved_feed(feed_id: str):
        return schemas.GithubActivityOut.from_entity(services.github.run_feed(feed_id))

    @app.post("/api/github/feeds/{feed_id}/contributions", response_model=schemas.GithubContributionsOut)
    def github_feed_contributions(feed_id: str, body: schemas.GithubContributionsIn):
        result = services.github.feed_contributions(feed_id, body.days, body.tz)
        return schemas.GithubContributionsOut.from_entity(result)

    @app.post("/api/github/events/query", response_model=schemas.GithubActivityOut)
    def query_github_events(body: schemas.GithubQueryIn):
        return schemas.GithubActivityOut.from_entity(services.github.run_query(_spec(body)))

    @app.post("/api/github/events/context", response_model=schemas.EventContextOut)
    def github_event_context(body: schemas.EventSubjectModel):
        subject = EventSubject(**body.model_dump())  # ValidationError -> 422
        return schemas.EventContextOut.model_validate(services.github.event_context(subject))

    @app.get("/api/github/orgs/{org}/repos", response_model=list[schemas.GithubRepoOut])
    def list_github_org_repos(org: str):
        repos = services.github.list_org_repos(org)
        return [schemas.GithubRepoOut.model_validate(repo) for repo in repos]

    # ----- Settings & images -------------------------------------------

    @app.get("/api/settings", response_model=schemas.SettingsModel)
    def get_settings():
        return schemas.SettingsModel.model_validate(services.settings.get_settings())

    @app.put("/api/settings", response_model=schemas.SettingsModel)
    def update_settings(body: schemas.SettingsUpdate):
        settings = services.settings.update_settings(theme=body.theme, github_polling=body.github_polling)
        return schemas.SettingsModel.model_validate(settings)

    @app.post("/api/images", response_model=schemas.ImageOut, status_code=201)
    async def upload_image(file: UploadFile):
        content = await file.read()
        url = services.images.save_image(content, file.filename or "")
        return schemas.ImageOut(url=url)

    # ----- Static frontend (mounted last so /api keeps precedence) ------

    images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=images_dir), name="images")
    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app
