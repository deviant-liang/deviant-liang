# GitHub Profile Generator

A small GitHub profile statistics generator inspired by the structure and philosophy of
[Andrew6rant/Andrew6rant](https://github.com/Andrew6rant/Andrew6rant).

It collects:

- repositories
- followers
- stars
- commits authored by the configured user
- added lines of code
- languages
- GitHub avatar

The result is rendered as `assets/profile.svg`.

## Requirements

- Python 3.10+
- GitHub Personal Access Token
- Pillow

## Setup

### PowerShell

```powershell
$env:GITHUB_USERNAME="your-github-username"
$env:GITHUB_TOKEN="your-github-token"
python today.py
```

### Bash

```bash
export GITHUB_USERNAME="your-github-username"
export GITHUB_TOKEN="your-github-token"
python today.py
```

The token is optional for some public API requests, but using a token gives substantially
more API capacity.

## GitHub Actions

Create repository secrets:

- `GITHUB_USERNAME`
- `GITHUB_TOKEN`

The workflow in `.github/workflows/update.yml` runs the generator and commits the generated
SVG and cache back to the repository.

## Output

```text
assets/profile.svg
cache/profile.json
cache/statistics.json
cache/commits.json
cache/loc.json
cache/avatar.png
```

## Design

The program intentionally stays small:

```text
configuration
    ↓
GitHub API
    ↓
GitHub data
    ↓
statistics + cache
    ↓
avatar
    ↓
SVG renderer
    ↓
main()
```

There is deliberately no large service/container architecture. The goal is readable,
small functions and a simple data flow.
