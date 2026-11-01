"""Settings context use cases."""

import pytest

from opsdeck.application.settings import SettingsService
from opsdeck.domain.errors import ValidationError
from opsdeck.domain.settings import AppSettings
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
    paused = service.update_settings(polling=False)
    assert (paused.theme, paused.polling) == ("paper", False)
    assert service.update_settings(theme="ember").polling is False


def test_intervals_change_alone_and_reject_what_is_not_offered():
    repository = InMemorySettingsRepository()
    service = SettingsService(repository)
    assert service.update_settings(github_every=120, polling=None).github_every == 120
    with pytest.raises(ValidationError):
        service.update_settings(github_every=7, theme="paper")
    assert (repository.load().github_every, repository.load().theme) == (120, "midnight")
