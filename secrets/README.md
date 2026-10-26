Local credentials mounted read-only into the container by `docker-compose.yaml`. Everything here except this file is ignored by git.

- `gcp-adc.json`: Google Cloud Application Default Credentials for the Logs tab, readable by the container user (`chmod 644`).
