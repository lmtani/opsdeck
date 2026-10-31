"""LogProvider implementations, one subpackage per LogSourceKind. Each spells
refinements in its backend's query language (expression.py), parses that
backend's entries into LogEntry (entries.py) and runs the query (provider.py).

- gcp/  Google Cloud Logging
"""
