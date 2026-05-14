#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
import urllib.request
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class Day:
    date: dt.date
    count: int


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def fetch_contributions(username: str, token: str) -> List[Day]:
    query = """
    query($login:String!) {
      user(login:$login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    body = json.dumps({"query": query, "variables": {"login": username}}).encode("utf-8")
    req = urllib.request.Request("https://api.github.com/graphql", data=body, method="POST")
    req.add_header("Authorization", f"bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "tajinder2317-smooth-snake")

    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    if "errors" in payload:
        raise RuntimeError(f"GitHub API errors: {payload['errors']}")

    weeks = (
        payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        or []
    )
    days: List[Day] = []
    for week in weeks:
        for d in week.get("contributionDays", []):
            days.append(
                Day(
                    date=dt.date.fromisoformat(d["date"]),
                    count=int(d["contributionCount"]),
                )
            )
    days.sort(key=lambda x: x.date)
    return days


def catmull_rom_spline(points: Sequence[Tuple[float, float]], samples_per_seg: int) -> List[Tuple[float, float]]:
    if len(points) < 4:
        return list(points)

    out: List[Tuple[float, float]] = []
    for i in range(1, len(points) - 2):
        p0, p1, p2, p3 = points[i - 1], points[i], points[i + 1], points[i + 2]
        for s in range(samples_per_seg):
            t = s / float(samples_per_seg)
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    out.append(points[-2])
    return out


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def contribution_color(count: int, theme: str) -> Tuple[int, int, int, int]:
    # Lightweight palettes inspired by GitHub.
    if theme == "dark":
        empty = (13, 17, 23, 0)  # transparent
        levels = [
            (22, 27, 34, 160),
            (0, 78, 67, 180),
            (0, 109, 50, 200),
            (38, 166, 65, 220),
            (57, 211, 83, 240),
        ]
    else:
        empty = (255, 255, 255, 0)
        levels = [
            (235, 237, 240, 200),
            (155, 233, 168, 220),
            (64, 196, 99, 230),
            (48, 161, 78, 235),
            (33, 110, 57, 245),
        ]

    if count <= 0:
        return empty
    if count == 1:
        return levels[0]
    if count <= 3:
        return levels[1]
    if count <= 6:
        return levels[2]
    if count <= 10:
        return levels[3]
    return levels[4]


def render_gif(
    days: Sequence[Day],
    out_path: str,
    theme: str,
    width: int = 900,
    height: int = 180,
) -> None:
    scale = 2  # render high-res then downsample for smoothing
    cell = 12 * scale
    gap = 3 * scale
    weeks = 53
    rows = 7

    grid_w = weeks * cell + (weeks - 1) * gap
    grid_h = rows * cell + (rows - 1) * gap

    pad_x = (width * scale - grid_w) // 2
    pad_y = (height * scale - grid_h) // 2

    # Map date -> count
    by_date = {d.date: d.count for d in days}
    start = days[0].date
    # Build a 53x7 grid (column major like GitHub)
    grid: List[List[Day]] = []
    idx = 0
    all_days: List[Day] = []
    for _w in range(weeks):
        col: List[Day] = []
        for _r in range(rows):
            day = days[idx] if idx < len(days) else days[-1]
            col.append(day)
            all_days.append(day)
            idx += 1
        grid.append(col)

    # Choose snake waypoints (non-zero days). Fallback to all days if too sparse.
    active = [d for d in all_days if d.count > 0]
    base = active if len(active) >= 24 else all_days

    points: List[Tuple[float, float]] = []
    for i, d in enumerate(base):
        col = i // rows
        row = i % rows
        x = pad_x + col * (cell + gap) + cell / 2
        y = pad_y + row * (cell + gap) + cell / 2
        points.append((x, y))

    # Pad endpoints for Catmull-Rom
    padded = [points[0]] + points + [points[-1]]
    path = catmull_rom_spline(padded, samples_per_seg=6)
    if len(path) < 10:
        path = points

    frames: List[Image.Image] = []
    num_frames = 90
    snake_len = max(120, int(len(path) * 0.25))

    bg = (13, 17, 23, 255) if theme == "dark" else (255, 255, 255, 255)
    snake_color = (0, 229, 255, 230) if theme == "dark" else (0, 122, 255, 220)
    glow = (0, 229, 255, 70) if theme == "dark" else (0, 122, 255, 60)

    for f in range(num_frames):
        t = f / float(num_frames - 1)
        head = int(lerp(0, len(path) - 1, t))
        tail = max(0, head - snake_len)

        img = Image.new("RGBA", (width * scale, height * scale), bg)
        draw = ImageDraw.Draw(img, "RGBA")

        # Heatmap
        for col in range(weeks):
            for row in range(rows):
                i = col * rows + row
                if i >= len(all_days):
                    continue
                d = all_days[i]
                c = contribution_color(d.count, theme)
                x0 = pad_x + col * (cell + gap)
                y0 = pad_y + row * (cell + gap)
                draw.rounded_rectangle((x0, y0, x0 + cell, y0 + cell), radius=4 * scale, fill=c)

        # Snake glow (soft)
        for i in range(tail, head, 3):
            x, y = path[i]
            r = (cell * 0.55)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=glow)

        # Snake body
        for i in range(tail, head, 2):
            x, y = path[i]
            r = (cell * 0.35) * (0.6 + 0.4 * (i - tail) / max(1, head - tail))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=snake_color)

        # Head
        hx, hy = path[head]
        hr = cell * 0.45
        draw.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=(snake_color[0], snake_color[1], snake_color[2], 255))
        # Eyes
        eye_r = cell * 0.08
        draw.ellipse((hx - cell * 0.18 - eye_r, hy - cell * 0.12 - eye_r, hx - cell * 0.18 + eye_r, hy - cell * 0.12 + eye_r), fill=(0, 0, 0, 140))
        draw.ellipse((hx + cell * 0.18 - eye_r, hy - cell * 0.12 - eye_r, hx + cell * 0.18 + eye_r, hy - cell * 0.12 + eye_r), fill=(0, 0, 0, 140))

        # Downsample for anti-alias
        small = img.resize((width, height), resample=Image.Resampling.LANCZOS)
        frames.append(small)

    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=50,
        loop=0,
        disposal=2,
        optimize=True,
    )


def main() -> int:
    username = os.environ.get("USERNAME") or os.environ.get("GITHUB_USERNAME") or ""
    if not username:
        username = _env("GITHUB_REPOSITORY").split("/")[0]
    token = _env("GITHUB_TOKEN")

    out_light = os.environ.get("OUT_LIGHT", "assets/smooth-snake.gif")
    out_dark = os.environ.get("OUT_DARK", "assets/smooth-snake-dark.gif")

    days = fetch_contributions(username=username, token=token)
    if not days:
        raise RuntimeError("No contribution days returned from API")

    os.makedirs(os.path.dirname(out_light) or ".", exist_ok=True)

    render_gif(days, out_light, theme="light")
    render_gif(days, out_dark, theme="dark")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        raise

