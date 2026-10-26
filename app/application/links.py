"""Use cases of the links context: cards, command cards and their images."""

from app.application.common import apply_order
from app.domain.ordering import next_position, ordered
from app.domain.errors import NotFoundError, ValidationError
from app.domain.links import CommandRunner, ImageStore, Link, LinkKind, LinkRepository

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class LinkService:
    def __init__(self, repository: LinkRepository):
        self._repository = repository

    def list_links(self) -> list[Link]:
        return ordered(self._repository.list_all())

    def reorder_links(self, ids: list[str]) -> None:
        apply_order(self._repository, ids)

    def create_link(self, **fields) -> Link:
        link = Link.new(position=next_position(self._repository.list_all()), **fields)
        self._repository.save(link)
        return link

    def update_link(self, link_id: str, **fields) -> Link:
        existing = self._repository.get(link_id)
        if existing is None:
            raise NotFoundError(f"Link '{link_id}' não encontrado")
        existing.update(**fields)
        self._repository.save(existing)
        return existing

    def delete_link(self, link_id: str) -> None:
        if not self._repository.delete(link_id):
            raise NotFoundError(f"Link '{link_id}' não encontrado")


class CommandService:
    """Runs the local command stored on a command-type link."""

    def __init__(self, runner: CommandRunner, links: LinkRepository):
        self._runner = runner
        self._links = links

    def run_link(self, link_id: str) -> str:
        link = self._links.get(link_id)
        if link is None:
            raise NotFoundError(f"Link '{link_id}' não encontrado")
        if link.kind != LinkKind.COMMAND or not link.command.strip():
            raise ValidationError("Este atalho não é um comando")
        return self._runner.run(link.command)


class ImageService:
    def __init__(self, store: ImageStore):
        self._store = store

    def save_image(self, content: bytes, filename: str) -> str:
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
            raise ValidationError(f"Formato inválido. Use: {allowed}")
        if len(content) > MAX_IMAGE_BYTES:
            raise ValidationError("Imagem muito grande (máximo 5 MB)")
        if not content:
            raise ValidationError("Arquivo vazio")
        return self._store.save(content, extension)
