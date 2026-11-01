"""Driven adapters: the implementations of the ports declared in opsdeck/domain/.

Ports and adapters: the domain and application layers only see the ports
(repositories, providers, stores); each subpackage here talks to one
kind of outside system and implements the ports that need it. The first level
is the capability, the second the concrete system, so a new backend for an
existing port is a new sibling folder plus one line in opsdeck/bootstrap.py.

- persistence/  the repository ports, on SQLite (default) or MySQL
- logs/         LogProvider, one subpackage per LogSourceKind (gcp/ today)
- github/       GithubProvider, over GitHub's REST, search and GraphQL APIs
- host/         ImageStore, on the machine running the app
"""
