# Repository Guidelines

## Project Structure & Module Organization

- `backend/` contains the Python application. `main.py` exposes FastAPI; service discovery and process control live in `services.py`, `scanner.py`, `process_info.py`, and `process_runner.py`; `desktop.py` starts the tray icon, pywebview windows, and local server.
- `frontend/` contains the build-free UI: HTML entry points, vanilla JavaScript in `frontend/js/`, CSS in `frontend/css/`, and icons in `frontend/assets/`.
- `docs/screenshots/` stores README and review images. Keep generated build output in `dist/` or `build/`; both are ignored by Git.
- `build.bat`/`build.spec` define the Windows PyInstaller single-executable package; `build.sh`/`build_mac.spec` define the macOS `.app` package. Persistent user data is outside the repository: `%LOCALAPPDATA%\Local Services Home` on Windows, `~/Library/Application Support/Local Services Home` on macOS.

## Build, Test, and Development Commands

Use Python 3.10+:

**Windows**

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m backend.desktop
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m backend.desktop
```

`python -m backend.desktop` launches the local server, tray process, and desktop windows. The frontend has no separate build step. Run `build.bat` on Windows or `./build.sh` on macOS from the repository root to install PyInstaller, clean `build/` and `dist/`, and produce `dist/本地服务管理.exe` or `dist/本地服务管理.app`.

## Coding Style & Naming Conventions

Follow the existing style: four-space indentation and type hints in Python, `snake_case` for Python names, two-space indentation and semicolons in JavaScript, `camelCase` for JavaScript names, and kebab-case HTML/CSS classes. Keep frontend code in the existing IIFE/strict-mode pattern and reuse CSS custom properties for themes. No formatter or linter is configured; match neighboring code.

## Testing Guidelines

No automated test suite currently exists. Before submitting backend changes, run `python -m compileall backend` and launch the app locally. Smoke-test `/api/health`, service listing, start/stop, port scanning, and the relevant UI window. For visual changes, verify both themes and include updated screenshots when the layout changes.

## Commit & Pull Request Guidelines

Recent commits use concise, action-oriented subjects, including version tags and prefixes such as `chore:` (for example, `v1.3.1: fix tray popup visibility...`). Keep subjects short, identify the affected area when useful, and avoid unrelated changes. Pull requests should explain the user-visible change, list validation, call out platform-specific behavior, link an issue when applicable, and include before/after screenshots for UI changes.

## Security & Configuration Tips

The manager is intended to bind to `127.0.0.1`. Do not expose it publicly or commit credentials, machine-specific paths, logs, or local service definitions. Review changes to script execution and process termination carefully; managed commands run with the user's OS permissions.
