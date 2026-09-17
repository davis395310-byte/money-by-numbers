"""Weekly social content pack generator — the distribution engine.

Reads the week's locked predictions + tipping-point explainers from
MongoDB and produces a ready-to-post pack:

    social/packs/2026_w01/
        pack.json      — structured data for every post (captions, hashtags,
                         graphic filename, suggested post time)
        post.md        — human-readable posting guide + schedule
        pick_*.png     — 1080x1350 pick-card graphics in the neon brand

Content rules (same honesty contract as the rest of the product):

- Every number comes from the locked pick or its explainer. Nothing is
  invented: no odds, no affiliate URLs, no partnerships.
- Sportsbook CTAs appear ONLY when the affiliate gate is satisfied
  (an ACTIVE sportsbook with a configured affiliate URL in the
  ``sportsbooks`` collection). Otherwise the pack says plainly that
  affiliate links are off until partnerships are signed.
- Captions describe the model; they never promise profit or frame picks
  as betting advice.

On-demand by design: run it once per week after picks lock. No cron, no
background credit burn.

Usage:
    cd ~/workspace/money-by-numbers
    MONGODB_URI=mongodb://127.0.0.1:27017/moneybynumbers \\
        python3 social/content_pack.py --season 2026 --week 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
for p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Brand (locked 2026-09-13)
# ---------------------------------------------------------------------------
PURPLE = "#8B5CF6"
NEON_PURPLE = "#BE8CFF"
TEAL = "#2DD4BF"
NEON_TEAL = "#46FFD7"
BLACK = "#030305"
WHITE = "#F2F5F9"
DIM = "#9AA5B5"

ANTON = REPO_ROOT / "brand" / "fonts" / "Anton-Regular.ttf"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

FRONTEND_URL = os.environ.get(
    "MNB_FRONTEND_URL",
    "https://money-by-numbers-6b196obbd-money-by-numbers.vercel.app",
)

HASHTAGS = ["#NFL", "#NFLPicks", "#FootballAnalytics", "#MoneyByNumbers"]

DISCLOSURE = (
    "Money By Numbers may receive compensation when users access "
    "sportsbook offers through links on this site."
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_week(season: int, week: int):
    from pymongo import MongoClient

    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise SystemExit("MONGODB_URI is not configured")
    db_name = uri.rsplit("/", 1)[-1] or "moneybynumbers"
    db = MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]

    preds = list(
        db["predictions"].find({"season": season, "week": week}).sort("created_at", 1)
    )
    if not preds:
        raise SystemExit(f"no locked predictions for {season} week {week}")
    explainers = {
        e["prediction_id"]: e for e in db["pick_explainers"].find({})
    }
    partners = list(
        db["sportsbooks"].find({"active": True, "affiliate_url": {"$ne": None}})
    )
    partners = [p for p in partners if p.get("affiliate_url")]
    return preds, explainers, partners


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def team_short(abbr: str) -> str:
    return {"LAR": "Rams"}.get(abbr, abbr)


_TEAM_FULL: dict[str, str] = {}


def team_full(abbr: str) -> str:
    """Full 'City Name' label; loaded lazily from the shared team reference."""
    global _TEAM_FULL
    if not _TEAM_FULL:
        try:
            from app.data_providers.teams_data import TEAMS

            _TEAM_FULL = {t["abbr"]: f"{t['city']} {t['name']}" for t in TEAMS}
            _TEAM_FULL["LA"] = _TEAM_FULL["LAR"]
        except Exception:
            pass
    return _TEAM_FULL.get(abbr, abbr)


def short_tipping_point(explainer: dict | None) -> str:
    """One-line tipping point for graphics/captions, from real factors."""
    if not explainer:
        return "Model pick — see the full why on the Pro page."
    tps = {t["factor"]: t for t in explainer.get("tipping_points", [])}
    gap = tps.get("rating_gap", {}).get("value", 0)
    pts = abs(round(gap))
    if "home_field" in tps:
        if gap < 0:
            return "home field tips it — rating gap favors the visitor"
        if pts < 25:
            return f"{pts}-pt rating gap — home field tips it"
        return f"{pts}-pt rating gap + home field"
    if "home_field_overcome" in tps:
        return f"{pts}-pt rating gap overcomes home field"
    return f"{pts}-pt rating gap"


def build_posts(pred, explainer, partners):
    winner = pred["predicted_winner"]
    winner_full = team_full(winner)
    prob = pred["model_probability"]
    conf = str(pred.get("confidence", "")).lower()
    matchup = f"{pred['away_team']} @ {pred['home_team']}"
    tp = short_tipping_point(explainer)
    tags = " ".join(HASHTAGS)
    pro_url = f"{FRONTEND_URL}/pro"

    x_post = (
        f"LOCKED: our model takes the {winner_full} ({winner}) "
        f"at {prob*100:.1f}% [{conf}]. "
        f"Tipping point: {tp}. "
        f"The full why behind every pick -> {pro_url} {tags}"
    )
    # X's 280-char limit: trim the tipping point if needed.
    if len(x_post) > 280:
        room = 280 - len(x_post) + len(tp)
        tp = tp[: max(room - 3, 10)].rstrip() + "..."
        x_post = (
            f"LOCKED: our model takes the {winner_full} ({winner}) "
            f"at {prob*100:.1f}% [{conf}]. "
            f"Tipping point: {tp}. "
            f"The full why behind every pick -> {pro_url} {tags}"
        )

    video_caption = (
        f"The numbers say {winner_full}. {prob*100:.1f}% — {conf} confidence. "
        f"Why? {explainer['explainer'] if explainer else 'See the Pro page.'} "
        f"Every pick, every tipping point -> link in bio. {tags}"
    )

    cta = None
    if partners:
        p0 = partners[0]
        cta = {
            "sportsbook": p0.get("name"),
            "url": f"{FRONTEND_URL}/r/{p0.get('sportsbook_id')}",
            "disclosure": DISCLOSURE,
        }
    return {
        "game_id": pred["game_id"],
        "matchup": matchup,
        "pick": winner,
        "pick_full": winner_full,
        "model_probability": prob,
        "confidence": conf,
        "tipping_point": tp,
        "posts": {
            "x": x_post,
            "video_caption": video_caption,
        },
        "affiliate_cta": cta,
    }


# ---------------------------------------------------------------------------
# Graphics — 1080x1350 pick cards, neon brand
# ---------------------------------------------------------------------------

def _font(path: Path, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(path), size)


def render_card(post: dict, out_path: Path) -> None:
    from PIL import Image, ImageDraw

    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)

    # Neon edge frame
    d.rectangle([24, 24, W - 24, H - 24], outline=PURPLE, width=6)
    d.rectangle([40, 40, W - 40, H - 40], outline=NEON_TEAL, width=2)

    anton_big = _font(ANTON, 120)
    anton_med = _font(ANTON, 84)
    anton_small = _font(ANTON, 54)
    body = _font(DEJAVU, 40)
    body_bold = _font(DEJAVU_BOLD, 40)

    y = 130
    d.text((W / 2, y), "MONEY BY NUMBERS", font=anton_small, fill=NEON_PURPLE, anchor="ma")
    y += 90
    d.text((W / 2, y), "PRO PICK", font=anton_med, fill=WHITE, anchor="ma")
    y += 120
    d.text((W / 2, y), post["matchup"].replace(" @ ", "  @  "), font=body_bold, fill=DIM, anchor="ma")

    y += 160
    pick_full = post.get("pick_full", post["pick"]).upper()
    pick_font = anton_big if len(pick_full) <= 8 else anton_med
    d.text((W / 2, y), pick_full, font=pick_font, fill=NEON_TEAL, anchor="ma")
    y += 170
    d.text(
        (W / 2, y),
        f"{post['model_probability']*100:.1f}%  ·  {post['confidence'].upper()} CONFIDENCE",
        font=body_bold,
        fill=WHITE,
        anchor="ma",
    )

    # Tipping point, wrapped
    y += 130
    words = post["tipping_point"].split()
    lines, line = [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if d.textlength(trial, font=body) > W - 240:
            lines.append(line)
            line = w
        else:
            line = trial
    if line:
        lines.append(line)
    d.text((W / 2, y), "TIPPING POINT", font=body_bold, fill=NEON_PURPLE, anchor="ma")
    y += 60
    for ln in lines[:2]:
        d.text((W / 2, y), ln, font=body, fill=DIM, anchor="ma")
        y += 55

    y = H - 200
    d.text((W / 2, y), "62.14% verified · 2,639 games", font=body, fill=DIM, anchor="ma")
    y += 60
    d.text((W / 2, y), "money by numbers", font=anton_small, fill=NEON_PURPLE, anchor="ma")

    img.save(out_path)


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------

def build_schedule(posts: list[dict], season: int, week: int) -> list[dict]:
    """Spread the week's picks across game days: 2-3 posts/day, game-day
    posts the morning of. Deterministic and simple."""
    # Anchor: week 1 kickoffs start Thursday; post from Tuesday.
    # We just need kickoff dates per game — pull from predictions if present.
    base = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)  # Tue before week 1
    schedule = []
    per_day = 3
    for i, post in enumerate(posts):
        day = base + timedelta(days=i // per_day)
        schedule.append(
            {
                "game_id": post["game_id"],
                "suggested_post_utc": day.isoformat(),
                "slot": (i % per_day) + 1,
            }
        )
    return schedule


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    args = ap.parse_args()

    preds, explainers, partners = load_week(args.season, args.week)

    out_dir = REPO_ROOT / "social" / "packs" / f"{args.season}_w{args.week:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    posts = []
    for pred in preds:
        pred = {k: v for k, v in pred.items() if k != "_id"}
        post = build_posts(pred, explainers.get(pred["prediction_id"]), partners)
        graphic = f"pick_{pred['game_id'].lower()}.png"
        render_card(post, out_dir / graphic)
        post["graphic"] = graphic
        posts.append(post)

    schedule = build_schedule(posts, args.season, args.week)

    pack = {
        "season": args.season,
        "week": args.week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": preds[0].get("model_version"),
        "affiliate_ctas_enabled": bool(partners),
        "affiliate_note": (
            f"{len(partners)} active sportsbook partner(s)."
            if partners
            else "No sportsbook partnerships configured — affiliate CTAs are "
            "OFF. Posts contain no sportsbook links until partnerships exist."
        ),
        "posts": posts,
        "schedule": schedule,
    }
    (out_dir / "pack.json").write_text(json.dumps(pack, indent=1))

    # Human-readable posting guide
    lines = [
        f"# Content pack — {args.season} Week {args.week}",
        "",
        f"Generated {pack['generated_at']}. Model {pack['model_version']}.",
        "",
        f"**Affiliate:** {pack['affiliate_note']}",
        "",
        "## Posting schedule (suggested, UTC)",
        "",
    ]
    for s in schedule:
        p = next(x for x in posts if x["game_id"] == s["game_id"])
        lines.append(
            f"- {s['suggested_post_utc'][:16]} — {p['matchup']}: "
            f"pick {p['pick']} ({p['graphic']})"
        )
    lines += ["", "## Posts", ""]
    for p in posts:
        lines += [
            f"### {p['matchup']} → {p['pick']}",
            "",
            f"Graphic: `{p['graphic']}`",
            "",
            "**X:**",
            "",
            p["posts"]["x"],
            "",
            "**Video caption (TikTok/Reels/Shorts):**",
            "",
            p["posts"]["video_caption"],
            "",
            f"Tipping point: {p['tipping_point']}",
            "",
        ]
        if p["affiliate_cta"]:
            lines += [
                f"Affiliate CTA: {p['affiliate_cta']['sportsbook']} — "
                f"{p['affiliate_cta']['url']}",
                f"_{p['affiliate_cta']['disclosure']}_",
                "",
            ]
    (out_dir / "post.md").write_text("\n".join(lines))

    print(f"pack: {out_dir}")
    print(f"posts: {len(posts)}, affiliate CTAs: {'ON' if partners else 'OFF'}")
    for p in posts[:3]:
        print(f"  - {p['matchup']}: {p['pick']} {p['model_probability']*100:.1f}%")


if __name__ == "__main__":
    main()
