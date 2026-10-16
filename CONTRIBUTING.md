# Contributing to Archian AI

Thanks for helping improve Archian AI.

## Developer Certificate of Origin (DCO)

By contributing, you certify that:

1. The contribution was created in whole or in part by you, and you have the right to submit it under the open source license indicated in this repository; or
2. The contribution is based on previous work that, to the best of your knowledge, is covered under an appropriate open source license, and you have the right to submit that work with modifications under that license; or
3. The contribution was provided directly to you by some other person who certified (1) or (2), and you have not modified it.

You also certify that you understand and agree that this project and the contribution are public, and that a record of the contribution (including your sign-off) is maintained indefinitely.

### Sign-off

Each commit must include a Signed-off-by line:

```text
Signed-off-by: Your Name <you@example.com>
```

Use `git commit -s` to add it automatically. The email should match your GitHub account.

## How to contribute

1. Fork the repo and create a branch from `main`.
2. Keep changes focused; match existing style in `backend/` and `frontend/`.
3. Add or update tests where practical (`backend/tests`, frontend Jest).
4. Run locally:
   - Backend: `cd backend && python -m pytest tests -q`
   - Frontend: `cd frontend && npm test -- --watchAll=false`
5. Open a pull request against `main` with a short summary and test notes.

## Architecture

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/MODULES.md](docs/MODULES.md), and [backend/AGENTS.md](backend/AGENTS.md) before changing planning, intent, or connector flows. Do not introduce keyword routing for planning.

## License

Contributions are licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).
