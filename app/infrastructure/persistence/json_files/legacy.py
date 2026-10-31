"""One-off upgrades of JSON files written by older versions of the app."""

from pathlib import Path

from app.domain.logs import LogSource, LogSourceKind, LogSourceRepository
from app.infrastructure.persistence.json_files.store import JsonStore


def adopt_legacy_projects(filters_path: Path, sources: LogSourceRepository) -> int:
    """Filters saved before log sources existed name a GCP project instead of
    a source: give each such project a gcp source (reusing one that reads the
    same project) and point the filters at it. Returns how many changed."""
    store = JsonStore(filters_path)
    records = store.read([])
    legacy = [record for record in records if record.get("project") and not record.get("source_id")]
    if not legacy:
        return 0
    by_project = {
        source.config["project"]: source.id
        for source in sources.list_all()
        if source.kind is LogSourceKind.GCP
    }
    for record in legacy:
        project = record.pop("project").strip()
        if project not in by_project:
            source = LogSource.new(name=project, kind=LogSourceKind.GCP, config={"project": project})
            sources.save(source)
            by_project[project] = source.id
        record["source_id"] = by_project[project]
    store.write(records)
    return len(legacy)
