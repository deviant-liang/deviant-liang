from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


# ============================================================
# Configuration
# ============================================================

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"

ROOT = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / "assets"
CACHE_DIR = ROOT / "cache"

PROFILE_CACHE = CACHE_DIR / "profile.json"
STATS_CACHE = CACHE_DIR / "statistics.json"
COMMITS_CACHE = CACHE_DIR / "commits.json"
LOC_CACHE = CACHE_DIR / "loc.json"
AVATAR_CACHE = CACHE_DIR / "avatar.png"

OUTPUT = ASSETS_DIR / "profile.svg"

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
PAGE_SIZE = 100

# Token is optional.
# If you have one:
#   PowerShell:
#   $env:GITHUB_TOKEN="ghp_xxx"
TOKEN = os.environ.get("GITHUB_TOKEN", "")


# ============================================================
# Visual direction
# ============================================================

BLACK = "#010302"
BLACK_2 = "#030705"
BLACK_3 = "#07100A"

GREEN = "#0A6F32"
GREEN_MID = "#0B8F40"
GREEN_BRIGHT = "#24C96B"
GREEN_FAINT = "#123D24"
GREEN_GHOST = "#0A2114"

WHITE = "#B9C9BE"
WHITE_SOFT = "#718178"

GRAY = "#405149"
GRAY_DARK = "#1B2921"
GRAY_DARKER = "#0B120E"

FONT = (
    "ui-monospace, SFMono-Regular, Menlo, Monaco, "
    "Consolas, monospace"
)

ASCII = " .,:;irsXA253hMHGS#9B&@"


# ============================================================
# Basic helpers
# ============================================================

def log(message: str) -> None:
    print(f"[profile] {message}")


def fail(message: str) -> None:
    print(f"\n[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def ensure_directories() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    temporary = path.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temporary.replace(path)


def esc(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ============================================================
# Git repository / username
# ============================================================

def get_git_remote() -> str:
    """
    Read the GitHub remote directly from the local repository.

    Supports:
        https://github.com/user/repo.git
        git@github.com:user/repo.git
        https://github.com/user/repo
        git@github.com:user/repo
    """

    try:
        result = subprocess.run(
            [
                "git",
                "remote",
                "get-url",
                "origin",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )

        remote = result.stdout.strip()

        if not remote:
            raise RuntimeError(
                "Git remote 'origin' is empty."
            )

        return remote

    except FileNotFoundError as error:
        raise RuntimeError(
            "Git executable was not found."
        ) from error

    except subprocess.CalledProcessError as error:
        message = (
            error.stderr.strip()
            or "Git remote 'origin' was not found."
        )

        raise RuntimeError(message) from error


def get_username_from_remote(remote: str) -> str:
    """
    Extract GitHub username/owner from the origin URL.

    This does NOT use HEAD, git log, or repository commits.
    Therefore it also works when the repository has no commits yet.
    """

    remote = remote.strip()

    patterns = (
        r"^https?://github\.com/([^/]+)/[^/]+/?(?:\.git)?$",
        r"^git@github\.com:([^/]+)/[^/]+/?(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+)/[^/]+/?(?:\.git)?$",
    )

    for pattern in patterns:
        match = re.match(
            pattern,
            remote,
            re.IGNORECASE,
        )

        if match:
            username = match.group(1).strip()

            if username:
                return username

    raise RuntimeError(
        f"Unable to determine GitHub username "
        f"from remote:\n{remote}"
    )


def get_username() -> str:
    remote = get_git_remote()

    log(f"Git remote: {remote}")

    username = get_username_from_remote(
        remote
    )

    log(f"GitHub username: @{username}")

    return username


# ============================================================
# GitHub API
# ============================================================

class GitHubAPIError(RuntimeError):
    def __init__(
        self,
        status,
        message,
        url,
    ):
        self.status = status
        self.message = message
        self.url = url

        super().__init__(
            f"GitHub API error {status}: {message}"
        )


class GitHubAPI:
    def __init__(self, token: str = ""):
        self.token = token

    def get(
        self,
        path: str,
        params: dict | None = None,
    ):
        url = API_URL + path

        if params:
            url += "?" + urlencode(params)

        headers = {
            "Accept": (
                "application/vnd.github+json"
            ),
            "User-Agent": (
                "deviant-liang-profile"
            ),
            "X-GitHub-Api-Version": API_VERSION,
        }

        if self.token:
            headers["Authorization"] = (
                f"Bearer {self.token}"
            )

        for attempt in range(
            REQUEST_RETRIES
        ):
            request = Request(
                url,
                headers=headers,
                method="GET",
            )

            try:
                with urlopen(
                    request,
                    timeout=REQUEST_TIMEOUT,
                ) as response:

                    remaining = (
                        response.headers.get(
                            "X-RateLimit-Remaining"
                        )
                    )

                    if remaining:
                        log(
                            f"API remaining: "
                            f"{remaining}"
                        )

                    body = response.read().decode(
                        "utf-8"
                    )

                    return json.loads(body)

            except HTTPError as error:
                body = error.read().decode(
                    "utf-8",
                    errors="replace",
                )

                if (
                    error.code in (403, 429)
                    and attempt
                    < REQUEST_RETRIES - 1
                ):
                    retry_after = (
                        error.headers.get(
                            "Retry-After"
                        )
                    )

                    try:
                        wait = int(
                            retry_after
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        wait = 2 ** attempt

                    log(
                        f"Rate limited. "
                        f"Retrying in {wait}s..."
                    )

                    time.sleep(wait)
                    continue

                raise GitHubAPIError(
                    error.code,
                    body,
                    url,
                ) from error

            except URLError as error:
                if (
                    attempt
                    < REQUEST_RETRIES - 1
                ):
                    wait = 2 ** attempt

                    log(
                        f"Network error. "
                        f"Retrying in {wait}s..."
                    )

                    time.sleep(wait)
                    continue

                raise GitHubAPIError(
                    None,
                    str(error.reason),
                    url,
                ) from error

        raise RuntimeError(
            "GitHub API request failed."
        )


# ============================================================
# GitHub data
# ============================================================

def get_profile(
    api: GitHubAPI,
    username: str,
) -> dict:

    log(
        f"Fetching profile @{username}"
    )

    profile = api.get(
        f"/users/{username}"
    )

    if not isinstance(
        profile,
        dict,
    ):
        raise GitHubAPIError(
            None,
            "Invalid profile response.",
            API_URL,
        )

    save_json(
        PROFILE_CACHE,
        profile,
    )

    return profile


def get_repositories(
    api: GitHubAPI,
    username: str,
) -> list[dict]:

    log("Fetching repositories...")

    repositories = []
    page = 1

    while True:
        batch = api.get(
            f"/users/{username}/repos",
            {
                "per_page": PAGE_SIZE,
                "page": page,
                "type": "owner",
                "sort": "updated",
            },
        )

        if not isinstance(
            batch,
            list,
        ):
            break

        if not batch:
            break

        repositories.extend(
            repo
            for repo in batch
            if isinstance(
                repo,
                dict,
            )
        )

        if len(batch) < PAGE_SIZE:
            break

        page += 1

    repositories = [
        repo
        for repo in repositories
        if not repo.get("fork")
        and not repo.get("archived")
        and not repo.get("disabled")
    ]

    log(
        f"Repositories: "
        f"{len(repositories)}"
    )

    return repositories


# ============================================================
# Commits
# ============================================================

def get_commit_count(
    api: GitHubAPI,
    username: str,
    repo: dict,
) -> int:

    if repo.get("size", 0) == 0:
        return 0

    owner = repo["owner"]["login"]
    name = repo["name"]

    query = (
        f"repo:{owner}/{name} "
        f"author:{username}"
    )

    try:
        result = api.get(
            "/search/commits",
            {
                "q": query,
                "per_page": 1,
            },
        )

        return int(
            result.get(
                "total_count",
                0,
            )
        )

    except GitHubAPIError as error:
        log(
            f"Commit count failed "
            f"for {name}: "
            f"{error.status}"
        )

        return 0


def get_commits(
    api: GitHubAPI,
    username: str,
    repositories: list[dict],
) -> int:

    cache = load_json(
        COMMITS_CACHE,
        {},
    )

    if not isinstance(
        cache,
        dict,
    ):
        cache = {}

    total = 0
    changed = False

    for repo in repositories:
        name = repo["name"]

        pushed_at = repo.get(
            "pushed_at"
        )

        cached = cache.get(name)

        if (
            isinstance(
                cached,
                dict,
            )
            and cached.get(
                "pushed_at"
            ) == pushed_at
        ):
            count = int(
                cached.get(
                    "count",
                    0,
                )
            )

        else:
            log(
                f"Calculating commits: "
                f"{name}"
            )

            count = get_commit_count(
                api,
                username,
                repo,
            )

            cache[name] = {
                "pushed_at": pushed_at,
                "count": count,
            }

            changed = True

        total += count

    if changed:
        save_json(
            COMMITS_CACHE,
            cache,
        )

    return total


# ============================================================
# LOC
# ============================================================

def get_lines_added_for_repo(
    api: GitHubAPI,
    username: str,
    repo: dict,
) -> int:

    if repo.get("size", 0) == 0:
        return 0

    owner = repo["owner"]["login"]
    name = repo["name"]

    try:
        data = api.get(
            f"/repos/{owner}/{name}"
            "/stats/contributors"
        )

    except GitHubAPIError as error:
        log(
            f"LOC unavailable "
            f"for {name}: "
            f"{error.status}"
        )

        return 0

    if not isinstance(
        data,
        list,
    ):
        return 0

    for contributor in data:
        if not isinstance(
            contributor,
            dict,
        ):
            continue

        author = (
            contributor.get(
                "author"
            )
            or {}
        )

        login = str(
            author.get(
                "login",
                "",
            )
        ).lower()

        if login != username.lower():
            continue

        return sum(
            int(
                week.get(
                    "a",
                    0,
                )
            )
            for week in contributor.get(
                "weeks",
                [],
            )
            if isinstance(
                week,
                dict,
            )
        )

    return 0


def get_lines_added(
    api: GitHubAPI,
    username: str,
    repositories: list[dict],
) -> int:

    cache = load_json(
        LOC_CACHE,
        {},
    )

    if not isinstance(
        cache,
        dict,
    ):
        cache = {}

    total = 0
    changed = False

    for repo in repositories:
        name = repo["name"]

        pushed_at = repo.get(
            "pushed_at"
        )

        cached = cache.get(name)

        if (
            isinstance(
                cached,
                dict,
            )
            and cached.get(
                "pushed_at"
            ) == pushed_at
        ):
            value = int(
                cached.get(
                    "value",
                    0,
                )
            )

        else:
            log(
                f"Calculating LOC: "
                f"{name}"
            )

            value = get_lines_added_for_repo(
                api,
                username,
                repo,
            )

            cache[name] = {
                "pushed_at": pushed_at,
                "value": value,
            }

            changed = True

        total += value

    if changed:
        save_json(
            LOC_CACHE,
            cache,
        )

    return total


# ============================================================
# Repository analytics
# ============================================================

def get_languages(
    repositories: list[dict],
) -> dict[str, int]:

    languages = {}

    for repo in repositories:
        language = repo.get(
            "language"
        )

        if not language:
            continue

        languages[language] = (
            languages.get(
                language,
                0,
            )
            + 1
        )

    return dict(
        sorted(
            languages.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    )


def get_stars(
    repositories: list[dict],
) -> int:

    return sum(
        int(
            repo.get(
                "stargazers_count",
                0,
            )
        )
        for repo in repositories
    )


def get_forks(
    repositories: list[dict],
) -> int:

    return sum(
        int(
            repo.get(
                "forks_count",
                0,
            )
        )
        for repo in repositories
    )


def get_issues(
    repositories: list[dict],
) -> int:

    return sum(
        int(
            repo.get(
                "open_issues_count",
                0,
            )
        )
        for repo in repositories
    )


def get_top_repositories(
    repositories: list[dict],
    count: int = 4,
) -> list[dict]:

    return sorted(
        repositories,
        key=lambda repo: (
            repo.get(
                "stargazers_count",
                0,
            ),
            repo.get(
                "forks_count",
                0,
            ),
            repo.get(
                "size",
                0,
            ),
        ),
        reverse=True,
    )[:count]


def get_latest_repository(
    repositories: list[dict],
) -> dict | None:

    if not repositories:
        return None

    return max(
        repositories,
        key=lambda repo: (
            repo.get(
                "pushed_at"
            )
            or ""
        ),
    )


# ============================================================
# Date helpers
# ============================================================

def parse_date(
    value: str | None,
) -> datetime | None:

    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None


def format_date(
    value: str | None,
) -> str:

    date = parse_date(value)

    if date is None:
        return "UNKNOWN"

    return date.strftime(
        "%Y.%m.%d"
    )


def account_age(
    created_at: str | None,
) -> str:

    created = parse_date(
        created_at
    )

    if created is None:
        return "UNKNOWN"

    now = datetime.now(
        timezone.utc
    )

    days = (
        now - created
    ).days

    years = days // 365
    months = (
        days % 365
    ) // 30

    if years:
        return (
            f"{years}Y "
            f"{months}M"
        )

    return f"{months}M"


# ============================================================
# Statistics
# ============================================================

def collect_statistics(
    username: str,
    profile: dict,
    repositories: list[dict],
    commits: int,
    lines_added: int,
) -> dict:

    latest = get_latest_repository(
        repositories
    )

    top_repositories = (
        get_top_repositories(
            repositories
        )
    )

    return {
        "username": username,

        "name": (
            profile.get("name")
            or username
        ),

        "bio": (
            profile.get("bio")
            or ""
        ),

        "location": (
            profile.get("location")
            or "UNKNOWN"
        ),

        "company": (
            profile.get("company")
            or "UNKNOWN"
        ),

        "repositories": len(
            repositories
        ),

        "followers": int(
            profile.get(
                "followers",
                0,
            )
        ),

        "following": int(
            profile.get(
                "following",
                0,
            )
        ),

        "stars": get_stars(
            repositories
        ),

        "forks": get_forks(
            repositories
        ),

        "issues": get_issues(
            repositories
        ),

        "commits": commits,

        "lines_added": lines_added,

        "languages": get_languages(
            repositories
        ),

        "account_created": format_date(
            profile.get(
                "created_at"
            )
        ),

        "account_age": account_age(
            profile.get(
                "created_at"
            )
        ),

        "latest_repo": (
            latest.get("name")
            if latest
            else "UNKNOWN"
        ),

        "latest_repo_date": (
            format_date(
                latest.get(
                    "pushed_at"
                )
            )
            if latest
            else "UNKNOWN"
        ),

        "top_repositories": [
            {
                "name": repo.get(
                    "name",
                    "UNKNOWN",
                ),

                "stars": int(
                    repo.get(
                        "stargazers_count",
                        0,
                    )
                ),

                "forks": int(
                    repo.get(
                        "forks_count",
                        0,
                    )
                ),

                "language": (
                    repo.get(
                        "language"
                    )
                    or "N/A"
                ),
            }
            for repo in top_repositories
        ],
    }


# ============================================================
# Avatar processing
# ============================================================

def download_avatar(
    profile: dict,
) -> Image.Image | None:

    avatar_url = profile.get(
        "avatar_url"
    )

    if not avatar_url:
        return load_cached_avatar()

    request = Request(
        avatar_url,
        headers={
            "User-Agent": (
                "deviant-liang-profile"
            )
        },
    )

    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            data = response.read()

        image = Image.open(
            io.BytesIO(data)
        ).convert("RGB")

        image.save(
            AVATAR_CACHE,
            "PNG",
        )

        return image

    except Exception as error:
        log(
            f"Avatar download failed: "
            f"{error}"
        )

        return load_cached_avatar()


def load_cached_avatar():
    if not AVATAR_CACHE.exists():
        return None

    try:
        return Image.open(
            AVATAR_CACHE
        ).convert("RGB")

    except Exception:
        return None


# ============================================================
# Large cinematic ASCII portrait
# ============================================================

def image_to_ascii(
    image: Image.Image | None,
    width: int = 72,
) -> list[str]:

    if image is None:
        return ["NO SIGNAL"]

    image = image.convert("RGB")

    if image.width <= 0 or image.height <= 0:
        return ["NO SIGNAL"]

    # --------------------------------------------------------
    # ASCII dimensions
    # --------------------------------------------------------

    source_ratio = (
        image.width / image.height
    )

    character_aspect = 0.60

    height = max(
        1,
        round(
            width
            / source_ratio
            * character_aspect
        ),
    )

    image = image.resize(
        (
            width,
            height,
        ),
        Image.Resampling.LANCZOS,
    )

    # --------------------------------------------------------
    # Grayscale
    # --------------------------------------------------------

    image = ImageOps.grayscale(
        image
    )

    # --------------------------------------------------------
    # Contrast
    # --------------------------------------------------------

    image = ImageOps.autocontrast(
        image,
        cutoff=0.5,
    )

    image = ImageEnhance.Contrast(
        image
    ).enhance(1.28)

    image = ImageEnhance.Brightness(
        image
    ).enhance(1.03)

    # --------------------------------------------------------
    # Controlled sharpening
    # --------------------------------------------------------

    image = image.filter(
        ImageFilter.UnsharpMask(
            radius=1.0,
            percent=110,
            threshold=4,
        )
    )

    # --------------------------------------------------------
    # Character ramp
    # --------------------------------------------------------

    characters = ASCII
    character_count = len(
        characters
    )

    pixels = list(
        image.getdata()
    )

    result: list[str] = []

    for y in range(height):
        line = []

        for x in range(width):
            value = pixels[
                y * width + x
            ]

            index = round(
                value
                / 255
                * (
                    character_count - 1
                )
            )

            index = max(
                0,
                min(
                    character_count - 1,
                    index,
                ),
            )

            line.append(
                characters[index]
            )

        result.append(
            "".join(line)
        )

    # --------------------------------------------------------
    # Remove empty rows only
    # --------------------------------------------------------

    while (
        result
        and not result[0].strip()
    ):
        result.pop(0)

    while (
        result
        and not result[-1].strip()
    ):
        result.pop()

    return result


# ============================================================
# SVG primitives
# ============================================================

def text(
    value,
    x,
    y,
    *,
    size=12,
    fill=WHITE,
    weight="400",
    opacity=1,
    anchor="start",
    letter_spacing=0,
    filter_id=None,
):

    filter_attr = (
        f' filter="url(#{filter_id})"'
        if filter_id
        else ""
    )

    return f"""
<text
  x="{x}"
  y="{y}"
  fill="{fill}"
  opacity="{opacity}"
  font-size="{size}px"
  font-weight="{weight}"
  text-anchor="{anchor}"
  letter-spacing="{letter_spacing}px"
  font-family="{FONT}"
  {filter_attr}
>{esc(value)}</text>
"""


def line(
    x1,
    y1,
    x2,
    y2,
    *,
    stroke=GRAY_DARK,
    opacity=1,
):

    return (
        f'<line '
        f'x1="{x1}" '
        f'y1="{y1}" '
        f'x2="{x2}" '
        f'y2="{y2}" '
        f'stroke="{stroke}" '
        f'opacity="{opacity}" />'
    )


def rect(
    x,
    y,
    width,
    height,
    *,
    fill="none",
    stroke="none",
    opacity=1,
    rx=0,
):

    return (
        f'<rect '
        f'x="{x}" '
        f'y="{y}" '
        f'width="{width}" '
        f'height="{height}" '
        f'fill="{fill}" '
        f'stroke="{stroke}" '
        f'opacity="{opacity}" '
        f'rx="{rx}" />'
    )


# ============================================================
# Cinematic background
# ============================================================

def create_background(
    width: int,
    height: int,
) -> str:

    svg = f"""
<defs>

  <radialGradient
    id="heroGlow"
    cx="30%"
    cy="42%"
    r="58%"
  >
    <stop
      offset="0%"
      stop-color="{GREEN}"
      stop-opacity="0.17"
    />

    <stop
      offset="45%"
      stop-color="{GREEN}"
      stop-opacity="0.05"
    />

    <stop
      offset="100%"
      stop-color="{BLACK}"
      stop-opacity="0"
    />
  </radialGradient>

  <linearGradient
    id="fadeTop"
    x1="0"
    y1="0"
    x2="0"
    y2="1"
  >
    <stop
      offset="0%"
      stop-color="{GREEN}"
      stop-opacity="0.08"
    />

    <stop
      offset="100%"
      stop-color="{BLACK}"
      stop-opacity="0"
    />
  </linearGradient>

  <linearGradient
    id="fadeRight"
    x1="0"
    y1="0"
    x2="1"
    y2="0"
  >
    <stop
      offset="0%"
      stop-color="{BLACK}"
      stop-opacity="0"
    />

    <stop
      offset="100%"
      stop-color="{GREEN}"
      stop-opacity="0.04"
    />
  </linearGradient>

  <filter
    id="softGlow"
    x="-50%"
    y="-50%"
    width="200%"
    height="200%"
  >
    <feGaussianBlur
      stdDeviation="2"
      result="blur"
    />

    <feMerge>
      <feMergeNode in="blur" />
      <feMergeNode in="SourceGraphic" />
    </feMerge>
  </filter>

</defs>

<rect
  width="100%"
  height="100%"
  fill="{BLACK}"
/>

<rect
  width="100%"
  height="100%"
  fill="url(#heroGlow)"
/>

<rect
  width="100%"
  height="100%"
  fill="url(#fadeTop)"
/>

<rect
  width="100%"
  height="100%"
  fill="url(#fadeRight)"
/>

<g
  stroke="{GREEN}"
  stroke-width="1"
  opacity="0.025"
>
"""

    for y in range(
        0,
        height,
        7,
    ):
        svg += (
            f'<line '
            f'x1="0" '
            f'y1="{y}" '
            f'x2="{width}" '
            f'y2="{y}" />'
        )

    svg += "</g>"

    svg += line(
        70,
        0,
        70,
        height,
        stroke=GREEN_GHOST,
        opacity=0.25,
    )

    return svg


# ============================================================
# Header
# ============================================================

def create_header(
    username: str,
) -> str:

    svg = ""

    svg += text(
        username.upper(),
        70,
        55,
        size=11,
        fill=GREEN_BRIGHT,
        weight="700",
        letter_spacing=3,
    )

    svg += text(
        "GITHUB / PROFILE",
        1040,
        55,
        size=9,
        fill=GRAY,
        anchor="end",
        letter_spacing=2,
    )

    svg += line(
        70,
        75,
        1040,
        75,
        stroke=GREEN_GHOST,
        opacity=0.7,
    )

    svg += text(
        "01",
        70,
        105,
        size=8,
        fill=GREEN,
        weight="700",
    )

    svg += text(
        "IDENTITY",
        102,
        105,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    return svg


# ============================================================
# Hero portrait
# ============================================================

def create_portrait(
    avatar: Image.Image | None,
) -> str:

    svg = ""

    x = 70
    y = 135
    width = 515
    height = 405

    svg += rect(
        x,
        y,
        width,
        height,
        fill=BLACK_2,
        stroke=GREEN_GHOST,
        opacity=0.8,
    )

    svg += text(
        "VISUAL IDENTITY",
        x + 22,
        y + 25,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    padding_left = 24
    padding_right = 24
    padding_top = 48
    padding_bottom = 18

    portrait_x = (
        x + padding_left
    )

    portrait_y = (
        y + padding_top
    )

    portrait_width = (
        width
        - padding_left
        - padding_right
    )

    portrait_height = (
        height
        - padding_top
        - padding_bottom
    )

    ascii_width = 78

    lines = image_to_ascii(
        avatar,
        width=ascii_width,
    )

    char_size = 8

    character_width = (
        char_size * 0.625
    )

    ascii_width_px = (
        ascii_width
        * character_width
    )

    ascii_height_px = (
        len(lines)
        * char_size
    )

    scale_x = (
        portrait_width
        / ascii_width_px
    )

    scale_y = (
        portrait_height
        / ascii_height_px
        if ascii_height_px > 0
        else 1.0
    )

    scale = min(
        scale_x,
        scale_y,
        1.0,
    )

    rendered_width = (
        ascii_width_px
        * scale
    )

    rendered_height = (
        ascii_height_px
        * scale
    )

    center_x = (
        portrait_x
        + portrait_width / 2
    )

    center_y = (
        portrait_y
        + portrait_height / 2
    )

    # IMPORTANT:
    # The ASCII text itself is left anchored.
    # This prevents the previous horizontal
    # centering error caused by text-anchor="middle".
    start_x = (
        center_x
        - rendered_width / 2
    )

    start_y = (
        center_y
        - rendered_height / 2
    )

    svg += f"""
<g
  transform="
    translate({start_x},{start_y})
    scale({scale})
  "
>
<text
  x="0"
  y="0"
  fill="{GREEN}"
  opacity="0.92"
  font-size="{char_size}px"
  font-weight="400"
  text-anchor="start"
  letter-spacing="0px"
  font-family="{FONT}"
  xml:space="preserve"
>
"""

    for index, value in enumerate(lines):
        dy = (
            char_size
            if index == 0
            else char_size
        )

        svg += (
            f'<tspan '
            f'x="0" '
            f'dy="{dy}px">'
            f'{esc(value)}'
            f'</tspan>'
        )

    svg += """
</text>
</g>
"""

    svg += line(
        x,
        y,
        x + 32,
        y,
        stroke=GREEN_BRIGHT,
        opacity=0.9,
    )

    svg += line(
        x,
        y,
        x,
        y + 32,
        stroke=GREEN_BRIGHT,
        opacity=0.9,
    )

    svg += line(
        x + width - 32,
        y + height,
        x + width,
        y + height,
        stroke=GREEN_BRIGHT,
        opacity=0.9,
    )

    svg += line(
        x + width,
        y + height - 32,
        x + width,
        y + height,
        stroke=GREEN_BRIGHT,
        opacity=0.9,
    )

    return svg


# ============================================================
# Identity
# ============================================================

def create_identity(
    stats: dict,
) -> str:

    svg = ""

    x = 635

    svg += text(
        "THE OPERATOR",
        x,
        145,
        size=9,
        fill=GREEN,
        weight="700",
        letter_spacing=3,
    )

    svg += text(
        stats["username"],
        x,
        188,
        size=37,
        fill=WHITE,
        weight="700",
        letter_spacing=1,
    )

    svg += text(
        "software engineer / systems / C++",
        x,
        216,
        size=10,
        fill=GRAY,
        letter_spacing=1,
    )

    svg += line(
        x,
        238,
        1040,
        238,
        stroke=GREEN_GHOST,
        opacity=0.8,
    )

    svg += text(
        "STATUS",
        x,
        270,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    svg += text(
        "ONLINE",
        x,
        295,
        size=13,
        fill=GREEN_BRIGHT,
        weight="700",
        letter_spacing=2,
        filter_id="softGlow",
    )

    svg += text(
        "LOCATION",
        x + 150,
        270,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    svg += text(
        stats["location"],
        x + 150,
        295,
        size=11,
        fill=WHITE,
    )

    svg += text(
        "SINCE",
        x,
        330,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    svg += text(
        stats["account_created"],
        x,
        354,
        size=11,
        fill=WHITE,
    )

    svg += text(
        "AGE",
        x + 150,
        330,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    svg += text(
        stats["account_age"],
        x + 150,
        354,
        size=11,
        fill=WHITE,
    )

    bio = stats["bio"]

    if bio:
        bio = bio.replace(
            "\n",
            " ",
        )

        if len(bio) > 60:
            bio = bio[:57] + "..."

        svg += text(
            bio,
            x,
            395,
            size=10,
            fill=WHITE_SOFT,
        )

    svg += text(
        f"github.com/{stats['username']}",
        x,
        430,
        size=9,
        fill=GREEN_MID,
        letter_spacing=1,
    )

    svg += text(
        "ACCESS LEVEL / PUBLIC",
        x,
        465,
        size=8,
        fill=GRAY,
        letter_spacing=1,
    )

    return svg


# ============================================================
# Repository signature
# ============================================================

def create_repository_signature(
    stats: dict,
) -> str:

    svg = ""

    x = 635

    svg += text(
        "REPOSITORY SIGNAL",
        x,
        515,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    y = 540

    for repo in stats[
        "top_repositories"
    ]:
        name = repo["name"]

        if len(name) > 18:
            name = name[:15] + "..."

        svg += text(
            name,
            x,
            y,
            size=9,
            fill=WHITE_SOFT,
        )

        svg += text(
            f"★ {repo['stars']}",
            x + 175,
            y,
            size=8,
            fill=GREEN,
        )

        svg += text(
            f"⑂ {repo['forks']}",
            x + 225,
            y,
            size=8,
            fill=GREEN_MID,
        )

        svg += text(
            repo["language"],
            x + 405,
            y,
            size=8,
            fill=GRAY,
            anchor="end",
        )

        y += 19

    return svg


# ============================================================
# Statistics strip
# ============================================================

def create_statistics(
    stats: dict,
) -> str:

    svg = ""

    y = 585

    svg += text(
        "02",
        70,
        y,
        size=8,
        fill=GREEN,
        weight="700",
    )

    svg += text(
        "SYSTEM METRICS",
        102,
        y,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    metrics = [
        (
            "REPOSITORIES",
            stats["repositories"],
        ),
        (
            "FOLLOWERS",
            stats["followers"],
        ),
        (
            "STARS",
            stats["stars"],
        ),
        (
            "COMMITS",
            stats["commits"],
        ),
        (
            "LINES ADDED",
            stats["lines_added"],
        ),
    ]

    x = 70

    for label, value in metrics:
        svg += text(
            label,
            x,
            625,
            size=7,
            fill=GRAY,
            letter_spacing=1,
        )

        svg += text(
            f"{value:,}",
            x,
            655,
            size=21,
            fill=WHITE,
            weight="700",
        )

        x += 190

    svg += line(
        70,
        675,
        1040,
        675,
        stroke=GRAY_DARK,
        opacity=0.8,
    )

    return svg


# ============================================================
# Languages
# ============================================================

def create_languages(
    stats: dict,
) -> str:

    svg = ""

    svg += text(
        "03",
        70,
        715,
        size=8,
        fill=GREEN,
        weight="700",
    )

    svg += text(
        "LANGUAGE SIGNATURE",
        102,
        715,
        size=8,
        fill=GRAY,
        letter_spacing=2,
    )

    languages = list(
        stats["languages"].items()
    )[:5]

    if not languages:
        return svg

    total = sum(
        count
        for _, count
        in languages
    )

    x = 70

    for language, count in languages:
        ratio = (
            count / total
            if total
            else 0
        )

        bar_width = max(
            8,
            int(
                ratio * 165
            ),
        )

        svg += rect(
            x,
            740,
            165,
            4,
            fill=GRAY_DARK,
        )

        svg += rect(
            x,
            740,
            bar_width,
            4,
            fill=GREEN,
        )

        svg += text(
            language.upper(),
            x,
            765,
            size=8,
            fill=WHITE_SOFT,
            letter_spacing=1,
        )

        svg += text(
            f"{count}",
            x + 165,
            765,
            size=8,
            fill=GREEN_MID,
            anchor="end",
        )

        x += 190

    return svg


# ============================================================
# Footer
# ============================================================

def create_footer(
    stats: dict,
) -> str:

    svg = ""

    svg += line(
        70,
        790,
        1040,
        790,
        stroke=GREEN_GHOST,
        opacity=0.7,
    )

    svg += text(
        "LATEST SIGNAL",
        70,
        815,
        size=7,
        fill=GRAY,
        letter_spacing=2,
    )

    svg += text(
        stats["latest_repo"],
        165,
        815,
        size=8,
        fill=WHITE_SOFT,
    )

    svg += text(
        stats["latest_repo_date"],
        400,
        815,
        size=8,
        fill=GRAY,
    )

    svg += text(
        "GITHUB API / LIVE DATA",
        1040,
        815,
        size=7,
        fill=GRAY,
        anchor="end",
        letter_spacing=1,
    )

    return svg


# ============================================================
# SVG
# ============================================================

def create_svg(
    stats: dict,
    avatar: Image.Image | None,
) -> str:

    width = 1110
    height = 850

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg
  xmlns="http://www.w3.org/2000/svg"
  width="{width}"
  height="{height}"
  viewBox="0 0 {width} {height}"
>

{create_background(
    width,
    height,
)}

{create_header(
    stats["username"]
)}

{create_portrait(
    avatar,
)}

{create_identity(
    stats,
)}

{create_repository_signature(
    stats,
)}

{create_statistics(
    stats,
)}

{create_languages(
    stats,
)}

{create_footer(
    stats,
)}

</svg>
"""


# ============================================================
# Main
# ============================================================

def main() -> None:

    ensure_directories()

    log(
        "Initializing cinematic "
        "GitHub profile..."
    )

    # --------------------------------------------------------
    # IMPORTANT
    #
    # Username is NOT hard-coded.
    #
    # It is read from:
    #
    #     git remote get-url origin
    #
    # No HEAD / git log / commit history is required here.
    # --------------------------------------------------------

    try:
        username = get_username()

    except RuntimeError as error:
        fail(
            "Unable to determine GitHub username:\n"
            f"{error}\n\n"
            "Make sure this repository has an "
            "'origin' GitHub remote."
        )

    api = GitHubAPI(
        TOKEN
    )

    try:
        profile = get_profile(
            api,
            username,
        )

        repositories = get_repositories(
            api,
            username,
        )

        commits = get_commits(
            api,
            username,
            repositories,
        )

        lines_added = get_lines_added(
            api,
            username,
            repositories,
        )

        stats = collect_statistics(
            username,
            profile,
            repositories,
            commits,
            lines_added,
        )

        save_json(
            STATS_CACHE,
            stats,
        )

        avatar = download_avatar(
            profile
        )

        svg = create_svg(
            stats,
            avatar,
        )

        OUTPUT.write_text(
            svg,
            encoding="utf-8",
        )

        log(
            f"Generated: {OUTPUT}"
        )

        print()

        print(
            "╭──────────────────────────────────────╮"
        )

        print(
            "│       CINEMATIC PROFILE READY        │"
        )

        print(
            "╰──────────────────────────────────────╯"
        )

        print(
            f"  @{username}"
        )

        print(
            f"  repositories : "
            f"{stats['repositories']:,}"
        )

        print(
            f"  followers    : "
            f"{stats['followers']:,}"
        )

        print(
            f"  stars        : "
            f"{stats['stars']:,}"
        )

        print(
            f"  commits      : "
            f"{stats['commits']:,}"
        )

        print(
            f"  lines added  : "
            f"{stats['lines_added']:,}"
        )

        print()

        print(
            "  output       : "
            "assets/profile.svg"
        )

        print()

    except GitHubAPIError as error:
        fail(
            "Unable to fetch GitHub data:\n"
            f"{error}"
        )


if __name__ == "__main__":
    main()