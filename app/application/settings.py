"""Use cases of the settings context."""

from app.domain.settings import AppSettings, SettingsRepository


class SettingsService:
    def __init__(self, repository: SettingsRepository):
        self._repository = repository

    def get_settings(self) -> AppSettings:
        return self._repository.load()

    def update_settings(self, theme: str) -> AppSettings:
        settings = self._repository.load()
        settings.theme = theme
        self._repository.save(settings)
        return settings
