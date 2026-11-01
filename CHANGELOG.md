# Changelog

Every release of OpsDeck, from its Conventional Commits. Install with `uv tool install git+https://github.com/lmtani/opsdeck@stable` and update with `uv tool upgrade opsdeck`; images are at `ghcr.io/lmtani/opsdeck`.

## 0.2.0 (2026-11-01)

### Features

- OpsDeck, a personal devops dashboard ([5155685](https://github.com/lmtani/opsdeck/commit/51556854d3a18a97aa866413725a8854b5bcda51))
- English UI, API messages and event wording ([9ff77e4](https://github.com/lmtani/opsdeck/commit/9ff77e4099efdb96d08f2932d790ed72774f395f))
- Build the image for arm64 as well as amd64 ([027b396](https://github.com/lmtani/opsdeck/commit/027b3962349b15e75e94bbbc7663c69855e1b980))
- Explain why a GitHub token does not work ([764ebed](https://github.com/lmtani/opsdeck/commit/764ebed448217f2fe05ce92cec672d7c41db7241))
- **github:** Desktop notifications for new feed events ([ee1ebfc](https://github.com/lmtani/opsdeck/commit/ee1ebfc645ae15130d31494cc440555301ae2919))
- **github:** Live switch to pause background updates ([9cf51f9](https://github.com/lmtani/opsdeck/commit/9cf51f92901791fea42acc992d1b4a300ae3c348))
- **github:** Confirm with a notification when a feed's bell is turned on ([062fbe2](https://github.com/lmtani/opsdeck/commit/062fbe243f2c83fc4d6bd1620ed0c8cd0dff1c14))
- **github:** Per-day contributors strip under the histogram ([30d5119](https://github.com/lmtani/opsdeck/commit/30d5119fbb2d550eb9ab2e59ccfab33f2ed59312))
- **github:** Side panel with the full context of an event ([4dc8a9b](https://github.com/lmtani/opsdeck/commit/4dc8a9bc54c66b6304f3eeaa0a0284cd829fde44))
- **github:** Conversation and pull request stats in the event panel ([6605abb](https://github.com/lmtani/opsdeck/commit/6605abbb57cb3762a4948d40a369c128b4688ddf))
- **web:** Installable PWA with offline notice and app badge ([b754a1a](https://github.com/lmtani/opsdeck/commit/b754a1a6ba89ca7aab17f3ddaf86d26f48e54775))
- **github:** Daily contributions per person from repository history ([592932e](https://github.com/lmtani/opsdeck/commit/592932e7b8fe6049d7f2cc8bb744bd11a6930dc8))
- **web:** Contributors mode in the GitHub tab ([aa76a65](https://github.com/lmtani/opsdeck/commit/aa76a65677f559fa0a95ff187e3a5a502b51e459))
- **github:** List what a person did on a contributors day ([4f35817](https://github.com/lmtani/opsdeck/commit/4f358172fd94665e96a5566204c339d0bf8a5460))
- **github:** Count a person's reviews of a pull request once per day ([c12c98a](https://github.com/lmtani/opsdeck/commit/c12c98a351281b2730a93ac7abb8c77ca91d8d5c))
- **web:** Clearer loading and arriving events in the GitHub tab ([385f66c](https://github.com/lmtani/opsdeck/commit/385f66c3d72be31ecdd40781522e46353ff0d040))
- **web:** Network mode in the GitHub tab ([9c4ed33](https://github.com/lmtani/opsdeck/commit/9c4ed3310f8ffdd32671cee04a07e1f32fdaf8df))
- **share:** Export and import cards, filters and feeds ([887a7d8](https://github.com/lmtani/opsdeck/commit/887a7d825ef88fd3b7891658b5f65a0d46c9dcf4))
- **web:** Export and import from the links, logs and GitHub views ([6eaccaf](https://github.com/lmtani/opsdeck/commit/6eaccaf1ea6af50827ade28c21ee01837c13e110))
- **web:** Severity histogram over the log results ([55fe28f](https://github.com/lmtani/opsdeck/commit/55fe28f4b9d72f73fb8c6d846aa962583fac8b39))
- **logs:** Log sources, with refinements written by the provider ([a6354c3](https://github.com/lmtani/opsdeck/commit/a6354c396fe739e1ffc1fdb2542f49fcfbdce6a2))
- **web:** Order the tabs GitHub, Logs, Links ([efdd243](https://github.com/lmtani/opsdeck/commit/efdd2430749839136b39fab4f71485e4848dcf85))
- **logs:** Watched filters count new entries in the background ([1d96316](https://github.com/lmtani/opsdeck/commit/1d963168cab4fbbb818eb880b07492e46a3553a7))
- **logs:** Show the new entries of a watched filter, whatever its window ([aade6e1](https://github.com/lmtani/opsdeck/commit/aade6e1f47060b0dc7976540b54eaead771a917b))
- **web:** Show when the next background update runs ([a837cf7](https://github.com/lmtani/opsdeck/commit/a837cf78bc28cbab180463a25c7238357e20a0b7))
- **settings:** Let the user choose how often background updates run ([81a9bb2](https://github.com/lmtani/opsdeck/commit/81a9bb2b88f68a409bdcc0cc9ab1bd0afcc9368a))
- **web:** Reopen the last tab on reload ([6e562d2](https://github.com/lmtani/opsdeck/commit/6e562d2cd262ca9a788f30ab50a51bcb7b03ca7f))
- Back up everything to one file and restore it ([f50eb0a](https://github.com/lmtani/opsdeck/commit/f50eb0aa773c9ec68f6e026748be397544b64b39))
- Install OpsDeck with `uv tool install` and keep its data in the user's data directory ([08f657c](https://github.com/lmtani/opsdeck/commit/08f657cebe0f03543fbf8f356d49575a18f9df4c))
- Release workflow and an in-app notice of new releases ([be75ff3](https://github.com/lmtani/opsdeck/commit/be75ff31dc1f0400e2f894c6441c87cc5e05360f))
- **release:** Release notes and changelog written by git-cliff from the Conventional Commits ([a133a6b](https://github.com/lmtani/opsdeck/commit/a133a6b7710eda96ce52f420d6fd8214ea8df627))
- **release:** A stable branch that follows releases, for uv tool upgrade ([2910fff](https://github.com/lmtani/opsdeck/commit/2910fff0833779411400a6e81645fd81ecc07865))

### Bug fixes

- Logs tab works in Docker and explains its errors ([206ef6c](https://github.com/lmtani/opsdeck/commit/206ef6c682388b0919ba7c2518ec588bab634eaa))
- **web:** Tray icons for export and import instead of bare arrows ([7e97f82](https://github.com/lmtani/opsdeck/commit/7e97f820244c74047e156077d1e6ed55a7373353))
- **web:** Align icon-only card buttons with the others ([a2dd506](https://github.com/lmtani/opsdeck/commit/a2dd506e49350c941b1b3f8df21691d8cd46938c))
- **web:** Keep the countdown line where it was after switching tabs ([0c9a709](https://github.com/lmtani/opsdeck/commit/0c9a7093e4b8af3c6469d18d6dd2e5fb25cdebd6))

### Performance

- **github:** Refresh event windows incrementally ([c43f6b1](https://github.com/lmtani/opsdeck/commit/c43f6b1f530e0e7d65ac3cb5bb9b5f5d228ae303))

### Refactoring

- **web:** Move the floating tooltip into a shared module ([c2a3503](https://github.com/lmtani/opsdeck/commit/c2a3503493d5758fb78bdec93b063a2ba350fb87))
- **logs:** Read logs through the Cloud Logging API only ([7392a77](https://github.com/lmtani/opsdeck/commit/7392a774c470c54eb631c6a6d3425ca9c63f75fe))
- **infrastructure:** One subpackage per outside system ([0046dda](https://github.com/lmtani/opsdeck/commit/0046dda9262d74fdc5f6edff3682eb3c31b93398))
- **github:** Split the provider into one reader per view ([2e2b665](https://github.com/lmtani/opsdeck/commit/2e2b66539f9a6d0acc905400308982694cf9c197))
- **persistence:** One SQL adapter for SQLite and MySQL ([c257df2](https://github.com/lmtani/opsdeck/commit/c257df2e85b4d825ac398a92e1a9938e09fc3303))

### Documentation

- Clearer setup for the GitHub and Logs tabs ([e0ba7b6](https://github.com/lmtani/opsdeck/commit/e0ba7b695465b7a40ff67691fc81cbd3b43b5528))
- Describe config sharing in CLAUDE.md ([35a9528](https://github.com/lmtani/opsdeck/commit/35a9528eda0058024d3c928bc8fbb299527600e8))
- Shorten the README to setup and move the feature tour to docs/ ([8cdcafe](https://github.com/lmtani/opsdeck/commit/8cdcafe5045c7294059dc6943d4ac5a29084bacf))
- Install notes for macOS ([e3d846f](https://github.com/lmtani/opsdeck/commit/e3d846f2d6821f20fbfa45c6447d81f434dcb7ea))

### Maintenance

- English docs, Docker Compose setup, no private infra ([b4fd701](https://github.com/lmtani/opsdeck/commit/b4fd701036014b2dbd4b4c7ec1ba3a3bb67be82e))
- Pre-commit hooks with ruff, mypy, Biome and gitleaks ([f1c6a87](https://github.com/lmtani/opsdeck/commit/f1c6a87177fec75502048698b5f56f9ec1c7e2b1))
- **security:** Audit dependencies and workflows, fix what they found ([dcbc637](https://github.com/lmtani/opsdeck/commit/dcbc6371b1fb42343bea8879e8f344aff045bc96))
- Install and serve the package on Apple silicon macOS too ([5565f26](https://github.com/lmtani/opsdeck/commit/5565f260be5b83ddd836859e3dadfc449af0c88d))

### Dependencies

- Bump the actions group with 6 updates ([49aa6f5](https://github.com/lmtani/opsdeck/commit/49aa6f50015a0f7ca10cf310c31e83b27e72220e))
- Bump the python group with 6 updates ([915bd49](https://github.com/lmtani/opsdeck/commit/915bd492af63027f4620f4c1ac4271a63133c8b1))
