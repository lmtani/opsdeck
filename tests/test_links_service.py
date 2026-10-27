"""Links context use cases: cards, command cards. In-memory fakes only."""

import pytest

from app.application.links import CommandService, ImageService, LinkService
from app.domain.errors import NotFoundError, ValidationError
from tests.fakes import FakeCommandRunner, FakeImageStore, InMemoryLinkRepository


def test_create_link_generates_id_and_persists():
    service = LinkService(InMemoryLinkRepository())
    link = service.create_link(title="  Console  ", url=" https://x ")
    assert link.id
    assert link.title == "Console"
    assert link.url == "https://x"
    assert service.list_links() == [link]


def test_create_link_requires_title_and_url():
    service = LinkService(InMemoryLinkRepository())
    with pytest.raises(ValidationError):
        service.create_link(title="  ", url="https://x")
    with pytest.raises(ValidationError):
        service.create_link(title="ok", url="")


def test_update_missing_link_raises_not_found():
    service = LinkService(InMemoryLinkRepository())
    with pytest.raises(NotFoundError):
        service.update_link("nope", title="a", url="b")


def test_delete_link_round_trip():
    service = LinkService(InMemoryLinkRepository())
    link = service.create_link(title="a", url="b")
    service.delete_link(link.id)
    assert service.list_links() == []
    with pytest.raises(NotFoundError):
        service.delete_link(link.id)


def test_reorder_links_persists_new_order():
    service = LinkService(InMemoryLinkRepository())
    first = service.create_link(title="a", url="u")
    second = service.create_link(title="b", url="u")
    third = service.create_link(title="c", url="u")

    service.reorder_links([third.id, first.id, second.id])

    assert [link.id for link in service.list_links()] == [third.id, first.id, second.id]


def test_new_link_lands_at_the_end_after_reorder():
    service = LinkService(InMemoryLinkRepository())
    first = service.create_link(title="a", url="u")
    second = service.create_link(title="b", url="u")
    service.reorder_links([second.id, first.id])

    third = service.create_link(title="c", url="u")

    assert [link.id for link in service.list_links()] == [second.id, first.id, third.id]


def test_create_command_link_requires_command_not_url():
    service = LinkService(InMemoryLinkRepository())
    link = service.create_link(title="IGV", kind="command", command=" igv.sh ")
    assert link.kind == "command"
    assert link.command == "igv.sh"
    assert link.url == ""


def test_create_command_link_without_command_raises():
    service = LinkService(InMemoryLinkRepository())
    with pytest.raises(ValidationError):
        service.create_link(title="IGV", kind="command", command="  ")


def test_create_link_rejects_unknown_kind():
    service = LinkService(InMemoryLinkRepository())
    with pytest.raises(ValidationError):
        service.create_link(title="x", url="https://x", kind="exec")


def test_run_link_executes_command():
    repository = InMemoryLinkRepository()
    links = LinkService(repository)
    link = links.create_link(title="IGV", kind="command", command="bash igv.sh")
    runner = FakeCommandRunner()
    service = CommandService(runner, repository)

    message = service.run_link(link.id)

    assert message == "Program started"
    assert runner.ran == ["bash igv.sh"]


def test_run_link_rejects_url_links():
    repository = InMemoryLinkRepository()
    link = LinkService(repository).create_link(title="Console", url="https://x")
    service = CommandService(FakeCommandRunner(), repository)
    with pytest.raises(ValidationError):
        service.run_link(link.id)


def test_run_link_unknown_id():
    service = CommandService(FakeCommandRunner(), InMemoryLinkRepository())
    with pytest.raises(NotFoundError):
        service.run_link("ghost")


def test_reorder_ignores_unknown_ids():
    service = LinkService(InMemoryLinkRepository())
    first = service.create_link(title="a", url="u")
    second = service.create_link(title="b", url="u")

    service.reorder_links(["ghost", second.id, first.id])

    assert [link.id for link in service.list_links()] == [second.id, first.id]


# ----- images ----------------------------------------------------------------


def test_save_image_stores_by_extension_and_returns_url():
    store = FakeImageStore()
    url = ImageService(store).save_image(b"\x89PNG", "Foto.PNG")
    assert url == "/images/1.png"
    assert store.saved == [(b"\x89PNG", "png")]


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (b"x", "script.sh", "Unsupported format"),
        (b"x", "noextension", "Unsupported format"),
        (b"", "empty.png", "Empty file"),
        (b"x" * (5 * 1024 * 1024 + 1), "large.jpg", "too large"),
    ],
)
def test_save_image_rejects_bad_uploads(content, filename, message):
    store = FakeImageStore()
    with pytest.raises(ValidationError, match=message):
        ImageService(store).save_image(content, filename)
    assert store.saved == []
