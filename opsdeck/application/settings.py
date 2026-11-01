"""Use cases of the settings context."""

from dataclasses import replace

from opsdeck.domain.settings import AppSettings, SettingsRepository


class SettingsService:
    def __init__(self, repository: SettingsRepository):
        self._repository = repository

    def get_settings(self) -> AppSettings:
        return self._repository.load()

    def update_settings(self, **changes) -> AppSettings:
        """Change only the preferences given (None = keep the saved value);
        an invalid value changes nothing."""
        given = {name: value for name, value in changes.items() if value is not None}
        settings = replace(self._repository.load(), **given)  # validates
        self._repository.save(settings)
        return settings
