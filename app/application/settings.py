"""Use cases of the settings context."""

from app.domain.settings import AppSettings, SettingsRepository


class SettingsService:
    def __init__(self, repository: SettingsRepository):
        self._repository = repository

    def get_settings(self) -> AppSettings:
        return self._repository.load()

    def update_settings(self, theme: str | None = None, polling: bool | None = None) -> AppSettings:
        """Change only the preferences given; the others keep their saved value."""
        settings = self._repository.load()
        if theme is not None:
            settings.theme = theme
        if polling is not None:
            settings.polling = polling
        self._repository.save(settings)
        return settings
