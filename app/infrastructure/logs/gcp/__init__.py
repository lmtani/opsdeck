"""The provider of `gcp` log sources: the Cloud Logging API with Application
Default Credentials. expression.py writes the filter language, the gcloud
command and the Logs Explorer URL; entries.py parses the API's entries."""

from app.infrastructure.logs.gcp.provider import CloudLoggingApiProvider

__all__ = ["CloudLoggingApiProvider"]
