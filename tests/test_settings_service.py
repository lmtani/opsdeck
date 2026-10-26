"""Settings context use cases."""

from app.application.settings import SettingsService
from app.domain.settings import AppSettings
from tests.fakes import InMemorySettingsRepository


def test_defaults_when_nothing_was_saved():
    assert SettingsService(InMemorySettingsRepository()).get_settings() == AppSettings()


def test_update_persists_theme():
    repository = InMemorySettingsRepository()
    service = SettingsService(repository)
    assert service.update_settings(theme="paper").theme == "paper"
    assert repository.load().theme == "paper"
    assert service.get_settings().theme == "paper"


def test_update_changes_only_what_is_given():
    service = SettingsService(InMemorySettingsRepository())
    service.update_settings(theme="paper")
    paused = service.update_settings(github_polling=False)
    assert (paused.theme, paused.github_polling) == ("paper", False)
    assert service.update_settings(theme="ember").github_polling is False
