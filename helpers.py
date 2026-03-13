# Disclosure: This project was co-created with Claude AI.
# It did not one-shot the project, it was a multi-step planning and thinking
# before the code was written. Even with that, we (Claude and I) have run into many issues
# and to fix them I had to understand what has been built and what was not. Most of the issues were fixed
# and needed even migration in the database from different tables. I have learned about green field and
# brown field differences and learned somethings in the hard way (such as droping a table on db...).
# Gladly, Github commits helped me to recover the data lost! it was an amazing learning experience!
# I will keep improving this web dev app and i'm aiming to host it on a domain soon! But I need to
# deliver this project into the final course, and now I believe it is in a good-enough state, and I'm proud of it!

# Thank you all CS50's staff!
# Here it goes the code:

import os

from cs50 import SQL
from flask import Flask, flash, redirect, render_template, url_for, request, session, jsonify
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash
from PIL import Image

from helpers import apology, login_required, role_required, dateformat

# Configure application
app = Flask(__name__)

# Custom filter
app.jinja_env.filters["dateformat"] = dateformat

# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Configure CS50 Library to use SQLite database
db = SQL("sqlite:///arnica.db")
UPLOAD_FOLDER = os.path.join("static", "uploads", "players")
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
PHOTO_SIZE = (256, 256)  # square crop


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def photo_path(person_id):
    """Return the static file path for a player photo, or None if not found."""
    for ext in ["jpg", "png", "webp"]:
        path = os.path.join(UPLOAD_FOLDER, f"{person_id}.{ext}")
        if os.path.exists(path):
            return f"/static/uploads/players/{person_id}.{ext}"
    return None


def save_photo(person_id, file):
    """Save and crop an uploaded photo to a square. Returns the URL path."""
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    # Remove any existing photo for this person
    for ext in ["jpg", "png", "webp"]:
        old = os.path.join(UPLOAD_FOLDER, f"{person_id}.{ext}")
        if os.path.exists(old):
            os.remove(old)

    dest = os.path.join(UPLOAD_FOLDER, f"{person_id}.jpg")
    img = Image.open(file)

    # Convert to RGB (handles PNG with alpha)
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Center-crop to square
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    img  = img.crop((left, top, left + side, top + side))
    img  = img.resize(PHOTO_SIZE, Image.LANCZOS)
    img.save(dest, "JPEG", quality=85)

    return f"/static/uploads/players/{person_id}.jpg"

def _secs(col, fallback_col="g.video_end", hard_default="01:30:00"):
    eff = f"COALESCE({col}, {fallback_col}, '{hard_default}')"
    return (
        f"(CAST(substr({eff},1,2) AS INT)*3600 +"
        f" CAST(substr({eff},4,2) AS INT)*60 +"
        f" CAST(substr({eff},7,2) AS INT))"
    )

def _secs_entered(col):
    return (
        f"(CAST(substr({col},1,2) AS INT)*3600 +"
        f" CAST(substr({col},4,2) AS INT)*60 +"
        f" CAST(substr({col},7,2) AS INT))"
    )


def recompute_roster_snapshots(game_id):
    """
    Rebuild roster_snapshots for a game from scratch.

    A snapshot represents the full active roster at a point in time.
    Each unique timestamp where the roster changes gets its own set of rows.
    At time T, the active roster = all rows where valid_from <= T,
    taking the LATEST valid_from per person.

    Algorithm:
    1. Load all roster_entries for this game, sorted by entered_at
    2. Collect all unique timestamps where changes happen
    3. At each timestamp, compute who is active on which team
    4. Write one row per active player per timestamp
    """

    # Clear existing snapshots for this game
    db.execute("DELETE FROM roster_snapshots WHERE game_id = ?", game_id)

    # Load all roster entries
    entries = db.execute("""
        SELECT re.person_id, re.team_id, re.entered_at, re.exited_at, re.is_goalkeeper,
               p.name
        FROM roster_entries re
        JOIN people p ON p.id = re.person_id
        WHERE re.game_id = ?
        ORDER BY re.entered_at ASC
    """, game_id)

    if not entries:
        return

    # Collect all unique change timestamps
    timestamps = sorted(set(
        t for e in entries
        for t in [e["entered_at"]]
        if t
    ))

    def time_to_secs(t):
        if not t:
            return float('inf')
        h, m, s = t.split(':')
        return int(h) * 3600 + int(m) * 60 + int(s)

    # At each timestamp, compute active roster:
    # active = entries where entered_at <= T and (exited_at is NULL or exited_at > T)
    # If a player has multiple active entries (shouldn't happen, but guard against it),
    # take the one with the latest entered_at.
    for ts in timestamps:
        ts_secs = time_to_secs(ts)
        active = {}  # person_id -> {team_id, is_goalkeeper, entered_at_secs}

        for e in entries:
            enter_secs = time_to_secs(e["entered_at"])
            exit_secs  = time_to_secs(e["exited_at"]) if e["exited_at"] else float('inf')

            if enter_secs <= ts_secs and exit_secs > ts_secs:
                pid = e["person_id"]
                # Keep latest entered_at if duplicates
                if pid not in active or enter_secs > active[pid]["enter_secs"]:
                    active[pid] = {
                        "team_id":      e["team_id"],
                        "is_goalkeeper": e["is_goalkeeper"],
                        "enter_secs":   enter_secs,
                    }

        # Write snapshot rows for this timestamp
        for pid, data in active.items():
            db.execute("""
                INSERT INTO roster_snapshots
                    (game_id, valid_from, team_id, person_id, is_goalkeeper)
                VALUES (?, ?, ?, ?, ?)
            """, game_id, ts, data["team_id"], pid, data["is_goalkeeper"])

def recompute_player_stats(person_id=None):
    """
    Rebuild player_stats_cache for one player or all players.

    Pass person_id=None to rebuild everyone (e.g. on first deploy).
    Pass a specific person_id to update just that player (fast path after a sub).

    Stats computed:
      field_seconds  — total seconds on field (team_id IS NOT NULL)
      gk_seconds     — total seconds as goalkeeper (is_goalkeeper = 1)

    exited_at NULL means the entry is still open; we use COALESCE with
    the game's video_end, falling back to 01:30:00 (90 min).
    """

    if person_id is not None:
        # Single player — fetch their roster entries with game video_end
        entries = db.execute("""
            SELECT re.person_id, re.team_id, re.is_goalkeeper,
                   re.entered_at, re.exited_at,
                   COALESCE(g.video_end, '01:30:00') AS game_end,
                   g.video_start,
                   pr.arrived_at
            FROM roster_entries re
            JOIN games g ON g.id = re.game_id
            LEFT JOIN presences pr ON pr.person_id = re.person_id AND pr.game_id = re.game_id
            WHERE re.person_id = ?
        """, person_id)
        _upsert_stats(person_id, entries)
    else:
        # All players — group by person
        all_entries = db.execute("""
            SELECT re.person_id, re.team_id, re.is_goalkeeper,
                   re.entered_at, re.exited_at,
                   COALESCE(g.video_end, '01:30:00') AS game_end,
                   g.video_start,
                   pr.arrived_at
            FROM roster_entries re
            JOIN games g ON g.id = re.game_id
            LEFT JOIN presences pr ON pr.person_id = re.person_id AND pr.game_id = re.game_id
            ORDER BY re.person_id
        """)

        from itertools import groupby
        for pid, group in groupby(all_entries, key=lambda r: r["person_id"]):
            _upsert_stats(pid, list(group))

        # Also clear cache for any player who no longer has roster entries
        db.execute("""
            DELETE FROM player_stats_cache
            WHERE person_id NOT IN (SELECT DISTINCT person_id FROM roster_entries)
        """)


def _upsert_stats(person_id, entries):
    """Compute field/gk/late seconds from a list of roster entry rows and upsert cache.

    late_seconds:  Only counted when arrived_at is explicitly set AND
                   arrived_at > video_start (or 00:00:00 if no video_start).
                   Computed once per game (not per roster entry) to avoid
                   double-counting players with multiple entries.
    late_arrivals: Count of games where the player was late.
    """

    def hms_to_secs(t):
        if not t:
            return 0
        parts = t.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    field_seconds = 0
    gk_seconds    = 0

    # Late time — deduplicate by game (entries may repeat same game/arrived_at)
    seen_games_for_late = {}   # game_end -> (arrived_at, video_start) first seen

    for e in entries:
        enter = hms_to_secs(e["entered_at"])
        exit_ = hms_to_secs(e["exited_at"]) if e["exited_at"] else hms_to_secs(e["game_end"])
        duration = max(0, exit_ - enter)

        game_end_secs = hms_to_secs(e["game_end"])
        duration = min(duration, max(0, game_end_secs - enter))
        if e["team_id"] is not None:
            field_seconds += duration
        if e["is_goalkeeper"] == 1:
            gk_seconds += duration

        # Collect late info per game (use game_end as a proxy key;
        # ideally game_id would be here — this is safe as long as game_end differs,
        # which it nearly always does. For exact dedup pass game_id in the query.)
        game_key = e["game_end"]
        if game_key not in seen_games_for_late:
            seen_games_for_late[game_key] = (e.get("arrived_at"), e.get("video_start"))

    # Compute late_seconds and late_arrivals
    late_seconds  = 0
    late_arrivals = 0
    for game_key, (arrived_at, video_start) in seen_games_for_late.items():
        # Only count if arrived_at is explicitly set
        if not arrived_at:
            continue
        kickoff = hms_to_secs(video_start) if video_start else 0
        late = max(0, hms_to_secs(arrived_at) - kickoff)
        if late > 0:
            late_seconds  += late
            late_arrivals += 1

    db.execute("""
        INSERT INTO player_stats_cache
            (person_id, field_seconds, gk_seconds, late_seconds, late_arrivals, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(person_id) DO UPDATE SET
            field_seconds  = excluded.field_seconds,
            gk_seconds     = excluded.gk_seconds,
            late_seconds   = excluded.late_seconds,
            late_arrivals  = excluded.late_arrivals,
            updated_at     = excluded.updated_at
    """, person_id, field_seconds, gk_seconds, late_seconds, late_arrivals)

def get_steps_done(db, game_id):
    """
    Return a set of step numbers (1–6) that have data for this game.
    Step 1 is always done once the game exists.
    Used to make future steps clickable in the game_steps macro.
    """
    if not game_id:
        return set()

    done = {1}  # game info always done if we have a game_id

    checks = [
        (2, "SELECT 1 FROM teams          WHERE game_id = ? LIMIT 1"),
        (3, "SELECT 1 FROM whatsapp_list  WHERE game_id = ? LIMIT 1"),
        (4, "SELECT 1 FROM presences      WHERE game_id = ? LIMIT 1"),
        (5, "SELECT 1 FROM roster_entries WHERE game_id = ? LIMIT 1"),
        (6, "SELECT 1 FROM roster_entries WHERE game_id = ? LIMIT 1"), #because once there is roster entries it should be ok to access events
    ]
    for step, sql in checks:
        if db.execute(sql, game_id):
            done.add(step)

    return done

def get_roster_timeline(game_id):
    """
    Return the full roster timeline as a structured dict for JSON serialization.

    Format:
    {
        "timestamps": ["00:00:00", "00:21:16", ...],
        "snapshots": {
            "00:00:00": [
                {"person_id": 1, "name": "...", "team_id": 6, "team_color": "black", "is_goalkeeper": 0},
                ...
            ],
            ...
        }
    }
    """
    rows = db.execute("""
        SELECT rs.valid_from, rs.person_id, rs.team_id, rs.is_goalkeeper,
               p.name, t.color AS team_color
        FROM roster_snapshots rs
        JOIN people p ON p.id = rs.person_id
        JOIN teams  t ON t.id = rs.team_id
        WHERE rs.game_id = ?
        ORDER BY rs.valid_from ASC, t.color, rs.is_goalkeeper DESC, p.name
    """, game_id)

    from collections import OrderedDict
    timeline = OrderedDict()
    for row in rows:
        ts = row["valid_from"]
        if ts not in timeline:
            timeline[ts] = []
        timeline[ts].append({
            "person_id":    row["person_id"],
            "name":         row["name"],
            "team_id":      row["team_id"],
            "team_color":   row["team_color"],
            "is_goalkeeper": row["is_goalkeeper"],
        })

    return {
        "timestamps": list(timeline.keys()),
        "snapshots":  dict(timeline),
    }

def _secs(col, fallback_col="g.video_end", hard_default="01:30:00"):
    eff = f"COALESCE({col}, {fallback_col}, '{hard_default}')"
    return (
        f"(CAST(substr({eff},1,2) AS INT)*3600 +"
        f" CAST(substr({eff},4,2) AS INT)*60 +"
        f" CAST(substr({eff},7,2) AS INT))"
    )

def _secs_entered(col):
    return (
        f"(CAST(substr({col},1,2) AS INT)*3600 +"
        f" CAST(substr({col},4,2) AS INT)*60 +"
        f" CAST(substr({col},7,2) AS INT))"
    )

@app.template_global()
def photo_url(person_id):
    """Return the URL for a player's photo, or None."""
    if not person_id:
        return None
    return photo_path(person_id)

@app.after_request
def after_request(response):
    """Ensure responses aren't cached"""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response

@app.route("/")
@login_required
def index():
    """Personal dashboard — show user's stats and recent events."""

    person_id = session.get("person_id")

    if not person_id:
        # Not linked to a player yet — show generic recent games
        recent_games = db.execute("""
            SELECT g.id, g.title, g.date, l.name AS location
            FROM games g
            JOIN locations l ON l.id = g.location_id
            ORDER BY g.date DESC
            LIMIT 5
        """)
        return render_template("index.html",
            linked=False,
            recent_games=recent_games
        )

    # Linked — fetch personal stats
    person = db.execute("SELECT * FROM people WHERE id = ?", person_id)[0]

    stats = db.execute("""
        SELECT
            COUNT(DISTINCT CASE WHEN e.type = 'goal' THEN e.id END) AS goals,
            COUNT(DISTINCT CASE WHEN el.link_type = 'assist' THEN el.id END) AS assists,
            COUNT(DISTINCT pr.game_id) AS appearances
        FROM people p
        LEFT JOIN events e       ON e.person_id = p.id
        LEFT JOIN event_links el ON el.linked_person_id = p.id
        LEFT JOIN presences pr   ON pr.person_id = p.id
        WHERE p.id = ?
    """, person_id)[0]

    # Recent events (goals and highlights)
    recent_events = db.execute("""
        SELECT
            e.*,
            g.title AS game_title,
            g.date  AS game_date,
            g.youtube_url
        FROM events e
        JOIN games g ON g.id = e.game_id
        WHERE e.person_id = ?
          AND e.type IN ('goal', 'highlight')
        ORDER BY g.date DESC, e.timestamp ASC
        LIMIT 10
    """, person_id)

    def build_embed_url(raw_url):
        """Convert any YouTube URL to an embed URL with enablejsapi=1."""
        if not raw_url:
            return None
        if "youtube.com/embed/" in raw_url:
            sep = "&" if "?" in raw_url else "?"
            return raw_url + sep + "enablejsapi=1"
        if "youtu.be/" in raw_url:
            vid = raw_url.split("youtu.be/")[1].split("?")[0]
        elif "v=" in raw_url:
            vid = raw_url.split("v=")[1].split("&")[0]
        else:
            return None
        return f"https://www.youtube.com/embed/{vid}?enablejsapi=1"

    for event in recent_events:
        event["links"] = db.execute("""
            SELECT el.link_type, pe.name
            FROM event_links el
            JOIN people pe ON pe.id = el.linked_person_id
            WHERE el.event_id = ?
        """, event["id"])
        event["youtube_embed"] = build_embed_url(event.get("youtube_url"))

    # Recent games the player appeared in
    recent_games = db.execute("""
        SELECT g.id, g.title, g.date, l.name AS location
        FROM presences pr
        JOIN games g     ON g.id = pr.game_id
        JOIN locations l ON l.id = g.location_id
        WHERE pr.person_id = ?
        ORDER BY g.date DESC
        LIMIT 5
    """, person_id)

    # Most recent game with a YouTube URL for the video panel
    recent_video_game = db.execute("""
        SELECT g.id, g.title, g.date, g.youtube_url
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = ? AND g.youtube_url IS NOT NULL AND g.youtube_url != ''
        ORDER BY g.date DESC
        LIMIT 1
    """, person_id)
    recent_video_game = recent_video_game[0] if recent_video_game else None

    # Build proper embed URL
    index_youtube_embed = build_embed_url(recent_video_game["youtube_url"]) if recent_video_game else None

    return render_template("index.html",
        linked=True,
        person=person,
        stats=stats,
        recent_events=recent_events,
        recent_games=recent_games,
        recent_video_game=recent_video_game,
        index_youtube_embed=index_youtube_embed,
    )


@app.route("/schema")
def schema():
    """Show db schema"""
    return render_template("schema.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""

    # Forget any user_id
    session.clear()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":
        # Ensure username was submitted
        if not request.form.get("username"):
            return apology("must provide username", 403)

        # Ensure password was submitted
        elif not request.form.get("password"):
            return apology("must provide password", 403)

        username = request.form.get("username").lower()

        # Query database for username
        rows = db.execute(
            "SELECT * FROM users WHERE username = ?", username
        )

        # Ensure username exists and password is correct
        if len(rows) != 1 or not check_password_hash(
            rows[0]["hash"], request.form.get("password")
        ):
            return apology("invalid username and/or password", 403)

        # Remember which user has logged in
        session["user_id"] = rows[0]["id"]
        session["role"] = rows[0]["role"]
        session["person_id"] = rows[0]["person_id"]


        # Redirect user to home page
        return redirect("/")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""

    # Forget any user_id
    session.clear()

    # Redirect user to login form
    return redirect("/")


@app.route("/userregister", methods=["GET", "POST"])
def userregister():
    """Register user"""
    if request.method == "GET":
        return render_template("userregister.html")

    else:
        # Access form data
        username = request.form.get("username").lower()
        if not username:
            return apology("No Username")

        password = request.form.get("password")
        if not password:
            return apology("No password")

        confirmation = request.form.get("confirmation")
        if not confirmation:
            return apology("No confirmation")

        if password != confirmation:
            return apology("Confirmation does not match password")

        hashpass = generate_password_hash(password)

        try:
            db.execute("INSERT INTO users (username, hash) VALUES(?, ?)", username, hashpass)
            return redirect("/")

        except ValueError:
            return apology("Username already exists")

@app.route("/games")
@login_required
def games():
    print(session.get("role"))
    """List all games, most recent first."""
    games = db.execute("""
            SELECT
                g.id,
                g.title,
                g.date,
                g.youtube_url,
                l.name AS location,
                COUNT(DISTINCT pr.person_id) AS player_count,
                COUNT(DISTINCT CASE WHEN e.type = 'goal' THEN e.id END) AS total_goals
            FROM games g
            JOIN locations l ON l.id = g.location_id
            LEFT JOIN presences pr ON pr.game_id = g.id
            LEFT JOIN events e ON e.game_id = g.id
            GROUP BY g.id
            ORDER BY g.date DESC
        """)
    return render_template("games.html", games=games)

@app.route("/games/<int:game_id>")
@login_required
def game(game_id):
    """Show a single game."""

    # ── 1. Game metadata ─────────────────────────────────────────
    game = db.execute("""
        SELECT g.*, l.name AS location
        FROM games g
        JOIN locations l ON l.id = g.location_id
        WHERE g.id = ?
    """, game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    # ── 2. All events in one query ───────────────────────────────
    events = db.execute("""
        SELECT e.*, pe.name AS person_name
        FROM events e
        LEFT JOIN people pe ON pe.id = e.person_id
        WHERE e.game_id = ?
        ORDER BY e.timestamp ASC
    """, game_id)

    # ── 3. All event_links in one query, grouped by event_id ─────
    all_links = db.execute("""
        SELECT el.event_id, el.link_type, pe.name
        FROM event_links el
        JOIN people pe ON pe.id = el.linked_person_id
        WHERE el.event_id IN (
            SELECT id FROM events WHERE game_id = ?
        )
    """, game_id)
    links_by_event = {}
    for lnk in all_links:
        links_by_event.setdefault(lnk["event_id"], []).append(lnk)

    # ── 4. All substitution_details in one query ─────────────────
    all_subs = db.execute("""
        SELECT sd.*,
               po.name AS player_off_name,
               pn.name AS player_on_name,
               t.color  AS team_color
        FROM substitution_details sd
        JOIN people po ON po.id = sd.player_off_id
        JOIN people pn ON pn.id = sd.player_on_id
        JOIN teams  t  ON t.id  = sd.team_id
        WHERE sd.event_id IN (
            SELECT id FROM events WHERE game_id = ? AND type = 'substitution'
        )
    """, game_id)
    subs_by_event = {s["event_id"]: s for s in all_subs}

    # ── 5. All team_change_details in one query ──────────────────
    all_tcd = db.execute("""
        SELECT tcd.*,
               tl.color AS leaving_color,
               te.color AS entering_color,
               ts.color AS staying_color
        FROM team_change_details tcd
        JOIN teams tl ON tl.id = tcd.leaving_team_id
        JOIN teams te ON te.id = tcd.entering_team_id
        LEFT JOIN teams ts ON ts.id = tcd.staying_team_id
        WHERE tcd.event_id IN (
            SELECT id FROM events WHERE game_id = ? AND type = 'team_change'
        )
    """, game_id)
    tcd_by_event = {t["event_id"]: t for t in all_tcd}

    # Attach details to each event
    for event in events:
        eid = event["id"]
        if event["type"] == "substitution":
            event["substitution"] = subs_by_event.get(eid)
            event["team_change"]  = None
            event["links"]        = []
        elif event["type"] == "team_change":
            event["team_change"]  = tcd_by_event.get(eid)
            event["substitution"] = None
            event["links"]        = []
        else:
            event["links"]        = links_by_event.get(eid, [])
            event["team_change"]  = None
            event["substitution"] = None

    # ── 6. Segments + scores — two queries total, not N+1 ────────
    segments = db.execute("""
        SELECT s.*, ta.color AS team_a_color, tb.color AS team_b_color,
               ta.id AS team_a_id, tb.id AS team_b_id
        FROM segments s
        JOIN teams ta ON ta.id = s.team_a_id
        JOIN teams tb ON tb.id = s.team_b_id
        WHERE s.game_id = ?
        ORDER BY s.started_at ASC
    """, game_id)

    # Single goals query — drives both segment scores and the JS live bar
    all_goals = db.execute("""
        SELECT e.timestamp, re.team_id
        FROM events e
        JOIN roster_entries re
          ON re.person_id  = e.person_id
         AND re.game_id    = e.game_id
         AND re.entered_at <= e.timestamp
         AND (re.exited_at IS NULL OR re.exited_at > e.timestamp)
        WHERE e.game_id = ?
          AND e.type    = 'goal'
        ORDER BY e.timestamp
    """, game_id)

    # Score each segment in Python — no extra queries
    for seg in segments:
        seg_start = seg["started_at"]
        seg_end   = seg["ended_at"] or "99:99:99"
        seg["score_a"] = sum(
            1 for g in all_goals
            if seg_start <= g["timestamp"] <= seg_end
            and int(g["team_id"] or 0) == int(seg["team_a_id"])
        )
        seg["score_b"] = sum(
            1 for g in all_goals
            if seg_start <= g["timestamp"] <= seg_end
            and int(g["team_id"] or 0) == int(seg["team_b_id"])
        )

    # ── 7. Roster timeline (single query inside helper) ──────────
    roster_timeline = get_roster_timeline(game_id)
    players = roster_timeline["snapshots"].get("00:00:00", [])

    # ── Seek target from ?t=HH:MM:SS ─────────────────────────────
    seek_seconds = None
    t_param = request.args.get("t", "").strip()
    if t_param:
        parts = t_param.split(":")
        if len(parts) == 3:
            try:
                seek_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                seek_seconds = None

    # ── YouTube embed ─────────────────────────────────────────────
    youtube_embed = None
    if game["youtube_url"]:
        url = game["youtube_url"]
        if "youtube.com/embed/" in url:
            separator = "&" if "?" in url else "?"
            base = url + separator + "enablejsapi=1"
        else:
            video_id = url.split("v=")[-1].split("&")[0]
            base = f"https://www.youtube.com/embed/{video_id}?enablejsapi=1"
        if seek_seconds is not None:
            base += f"&start={seek_seconds}"
        youtube_embed = base

    import json
    game_start_event = db.execute(
        "SELECT timestamp FROM events WHERE game_id = ? AND type = 'game_start' LIMIT 1",
        game_id
    )
    game_start_event = game_start_event[0] if game_start_event else None

    return render_template("game.html",
        game=game,
        events=events,
        segments=segments,
        players=players,
        roster_timeline_json=json.dumps(roster_timeline),
        youtube_embed=youtube_embed,
        seek_seconds=seek_seconds,
        all_goals_json=json.dumps([dict(g) for g in all_goals]),
        game_start_event=game_start_event,
    )

@app.route("/games/register", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def register_game():
    """Register a new game."""

    # Always fetch locations for the dropdown
    locations = db.execute("SELECT id, name FROM locations ORDER BY name")

    if request.method == "GET":
        return render_template("register_game.html", locations=locations)

    # POST — validate and insert
    title       = request.form.get("title", "").strip()
    date        = request.form.get("date", "").strip()
    location_id = request.form.get("location_id", "").strip()
    youtube     = request.form.get("youtube_url", "").strip()
    vid_start   = request.form.get("video_start", "").strip()
    vid_end     = request.form.get("video_end", "").strip()

    if not title:
        return apology("Game title is required")
    if not date:
        return apology("Date is required")
    if not location_id:
        return apology("Location is required")

    game_id = db.execute("""
        INSERT INTO games (title, date, location_id, youtube_url, video_start, video_end)
        VALUES (?, ?, ?, ?, ?, ?)
    """, title, date, location_id,
        youtube or None,
        vid_start or None,
        vid_end or None
    )

    return redirect(f"/games/{game_id}/teams")

@app.route("/games/<int:game_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def game_edit(game_id):
    game = db.execute("SELECT * FROM games WHERE id = ?", game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    locations = db.execute("SELECT * FROM locations ORDER BY name")

    if request.method == "GET":
        steps_done = get_steps_done(db, game_id)
        return render_template("game_edit.html", game=game, locations=locations, steps_done=steps_done)

    title       = request.form.get("title", "").strip()
    date        = request.form.get("date", "").strip()
    location_id = request.form.get("location_id", "").strip()
    youtube     = request.form.get("youtube_url", "").strip() or None
    vid_start   = request.form.get("video_start", "").strip() or None
    vid_end     = request.form.get("video_end", "").strip() or None

    if not title or not date or not location_id:
        return apology("Title, date and location are required")

    db.execute("""
        UPDATE games SET title = ?, date = ?, location_id = ?,
                         youtube_url = ?, video_start = ?, video_end = ?
        WHERE id = ?
    """, title, date, location_id, youtube, vid_start, vid_end, game_id)

    flash("Game updated!")
    return redirect(f"/games/{game_id}")

@app.route("/games/<int:game_id>/teams", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def manage_teams(game_id):
    """Define teams for a game."""

    game = db.execute("SELECT * FROM games WHERE id = ?", game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    teams = db.execute("SELECT * FROM teams WHERE game_id = ? ORDER BY id", game_id)

    # Existing initial segment (started_at = 00:00:00) if any
    initial_segment = db.execute("""
        SELECT * FROM segments WHERE game_id = ? AND started_at = '00:00:00'
    """, game_id)
    initial_segment = initial_segment[0] if initial_segment else None

    if request.method == "GET":
        steps_done = get_steps_done(db, game_id)
        return render_template("teams.html",
            game=game,
            teams=teams,
            steps_done=steps_done,
            initial_segment=initial_segment
        )

    action = request.form.get("action")

    # Add a team
    if action == "add":
        color  = request.form.get("color", "").strip().lower()
        custom = request.form.get("custom_color", "").strip().lower()
        if color == "custom":
            color = custom
        if not color:
            flash("Please enter a team color.")
            return redirect(f"/games/{game_id}/teams")
        try:
            db.execute("INSERT INTO teams (game_id, color) VALUES (?, ?)", game_id, color)
        except Exception:
            flash(f"'{color}' already exists for this game.")
        return redirect(f"/games/{game_id}/teams")

    # Delete a team
    if action == "delete":
        team_id = request.form.get("team_id")
        db.execute("DELETE FROM teams WHERE id = ? AND game_id = ?", team_id, game_id)
        # If the deleted team was part of the initial segment, remove it too
        db.execute("""
            DELETE FROM segments
            WHERE game_id = ? AND started_at = '00:00:00'
              AND (team_a_id = ? OR team_b_id = ?)
        """, game_id, team_id, team_id)
        return redirect(f"/games/{game_id}/teams")

    # Set starting matchup
    if action == "set_start":
        team_a_id = request.form.get("start_team_a", "").strip()
        team_b_id = request.form.get("start_team_b", "").strip()

        if not team_a_id or not team_b_id:
            flash("Please select both starting teams.")
            return redirect(f"/games/{game_id}/teams")
        if team_a_id == team_b_id:
            flash("Starting teams must be different.")
            return redirect(f"/games/{game_id}/teams")

        # Replace or create the initial segment
        db.execute("DELETE FROM segments WHERE game_id = ? AND started_at = '00:00:00'", game_id)
        db.execute("""
            INSERT INTO segments (game_id, team_a_id, team_b_id, started_at)
            VALUES (?, ?, ?, '00:00:00')
        """, game_id, team_a_id, team_b_id)
        return redirect(f"/games/{game_id}/teams")

    # Proceed to next step
    if action == "next":
        if len(teams) < 2:
            flash("You need at least 2 teams before continuing.")
            return redirect(f"/games/{game_id}/teams")
        if not initial_segment:
            # Check again in case it was just set
            seg = db.execute("SELECT id FROM segments WHERE game_id = ? AND started_at = '00:00:00'", game_id)
            if not seg:
                flash("Please set the starting matchup before continuing.")
                return redirect(f"/games/{game_id}/teams")
        return redirect(f"/games/{game_id}/attendance")

    return redirect(f"/games/{game_id}/teams")

@app.route("/games/<int:game_id>/attendance", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def attendance(game_id):
    """Manage whatsapp attendance list for a game."""

    game = db.execute("SELECT * FROM games WHERE id = ?", game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    if request.method == "GET":
        # All people, with a flag if they're already on the list
        people = db.execute("""
            SELECT p.id, p.name, p.nickname,
                   CASE WHEN w.person_id IS NOT NULL THEN 1 ELSE 0 END AS on_list
            FROM people p
            LEFT JOIN whatsapp_list w
                   ON w.person_id = p.id AND w.game_id = ?
            ORDER BY p.name
        """, game_id)
        steps_done = get_steps_done(db, game_id)
        return render_template("attendance.html", game=game, people=people, steps_done=steps_done)

    # POST — rebuild the whatsapp_list for this game
    selected = request.form.getlist("person_id")  # list of checked person ids

    # Remove all existing entries for this game and reinsert
    db.execute("DELETE FROM whatsapp_list WHERE game_id = ?", game_id)
    for pid in selected:
        db.execute("""
            INSERT INTO whatsapp_list (game_id, person_id, timestamp)
            VALUES (?, ?, datetime('now'))
        """, game_id, pid)

    return redirect(f"/games/{game_id}/presences")

@app.route("/games/<int:game_id>/presences", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def presences(game_id):
    """Manage who attended the game (no team assignment here — done via roster in events)."""

    game = db.execute("SELECT * FROM games WHERE id = ?", game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    if request.method == "GET":
        # People on the whatsapp list
        people = db.execute("""
            SELECT p.id, p.name, p.nickname,
                   CASE WHEN pr.person_id IS NOT NULL THEN 1 ELSE 0 END AS present,
                   pr.arrived_at
            FROM people p
            LEFT JOIN whatsapp_list w ON w.person_id = p.id AND w.game_id = ?
            LEFT JOIN presences pr    ON pr.person_id = p.id AND pr.game_id = ?
            WHERE w.person_id IS NOT NULL
            ORDER BY p.name
        """, game_id, game_id)

        # Others not on whatsapp list
        others = db.execute("""
            SELECT p.id, p.name, p.nickname,
                   CASE WHEN pr.person_id IS NOT NULL THEN 1 ELSE 0 END AS present,
                   pr.arrived_at
            FROM people p
            LEFT JOIN whatsapp_list w ON w.person_id = p.id AND w.game_id = ?
            LEFT JOIN presences pr    ON pr.person_id = p.id AND pr.game_id = ?
            WHERE w.person_id IS NULL
            ORDER BY p.name
        """, game_id, game_id)

        steps_done = get_steps_done(db, game_id)
        return render_template("presences.html",
            steps_done=steps_done,
            game=game,
            people=people,
            others=others
        )

    # POST — rebuild presences
    selected = request.form.getlist("person_id")

    db.execute("DELETE FROM presences WHERE game_id = ?", game_id)
    for pid in selected:
        arrived_at = request.form.get(f"arrived_at_{pid}", "").strip() or None
        # Validate HH:MM:SS format
        if arrived_at:
            import re
            if not re.match(r'^\d{2}:\d{2}:\d{2}$', arrived_at):
                arrived_at = None
        db.execute("""
            INSERT INTO presences (game_id, person_id, arrived_at)
            VALUES (?, ?, ?)
        """, game_id, pid, arrived_at)

    return redirect(f"/games/{game_id}/roster")


@app.route("/games/<int:game_id>/roster", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def manage_roster(game_id):
    """Assign players to teams with goalkeeper designation."""

    game = db.execute("SELECT * FROM games WHERE id = ?", game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    teams = db.execute("SELECT * FROM teams WHERE game_id = ? ORDER BY id", game_id)
    if not teams:
        flash("Define teams before setting the roster.")
        return redirect(f"/games/{game_id}/teams")

    # All players who attended, with their current starting roster entry if any
    players = db.execute("""
        SELECT p.id, p.name, p.nickname,
               re.team_id, re.is_goalkeeper
        FROM presences pr
        JOIN people p ON p.id = pr.person_id
        LEFT JOIN roster_entries re
               ON re.person_id = p.id
              AND re.game_id   = ?
              AND re.entered_at = '00:00:00'
        WHERE pr.game_id = ?
        ORDER BY p.name
    """, game_id, game_id)

    if request.method == "GET":
        steps_done = get_steps_done(db, game_id)
        return render_template("roster.html", game=game, teams=teams, players=players, steps_done=steps_done)

    # POST — validate GK per team, rebuild starting entries
    errors = []
    assignments = {}  # person_id -> {team_id, is_goalkeeper}

    for player in players:
        pid          = str(player["id"])
        team_id      = request.form.get(f"team_{pid}", "").strip()
        is_goalkeeper = 1 if request.form.get(f"gk_{pid}") else 0
        if team_id:
            assignments[pid] = {"team_id": team_id, "is_goalkeeper": is_goalkeeper}

    # Check each team has at least one GK
    from collections import defaultdict
    gk_per_team = defaultdict(int)
    players_per_team = defaultdict(int)
    for pid, data in assignments.items():
        tid = data["team_id"]
        players_per_team[tid] += 1
        if data["is_goalkeeper"]:
            gk_per_team[tid] += 1

    for team in teams:
        tid = str(team["id"])
        if players_per_team[tid] > 0 and gk_per_team[tid] == 0:
            errors.append(f"Team '{team['color']}' needs at least one goalkeeper.")

    if errors:
        for err in errors:
            flash(err)
        steps_done = get_steps_done(db, game_id)
        return render_template("roster.html", game=game, teams=teams, players=players, steps_done=steps_done)

    # Rebuild starting roster entries
    db.execute("""
        DELETE FROM roster_entries
        WHERE game_id = ? AND entered_at = '00:00:00'
    """, game_id)

    for pid, data in assignments.items():
        db.execute("""
            INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
            VALUES (?, ?, ?, '00:00:00', ?)
        """, game_id, pid, data["team_id"], data["is_goalkeeper"])

    recompute_player_stats()
    recompute_roster_snapshots(game_id)
    return redirect(f"/games/{game_id}/events")

@app.route("/games/<int:game_id>/events", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def log_events(game_id):
    """Live event logging for a game."""

    game = db.execute("""
        SELECT g.*, l.name AS location
        FROM games g
        JOIN locations l ON l.id = g.location_id
        WHERE g.id = ?
    """, game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    teams = db.execute("SELECT * FROM teams WHERE game_id = ? ORDER BY id", game_id)

    # Players on the roster (with their current starting team)
    players = db.execute("""
        SELECT p.id, p.name, p.nickname, t.color AS team_color, t.id AS team_id
        FROM roster_entries re
        JOIN people p ON p.id = re.person_id
        LEFT JOIN teams t ON t.id = re.team_id
        WHERE re.game_id = ? AND re.entered_at = '00:00:00'
        ORDER BY t.color, p.name
    """, game_id)

    if request.method == "GET":
        events = db.execute("""
            SELECT e.*, pe.name AS person_name
            FROM events e
            LEFT JOIN people pe ON pe.id = e.person_id
            WHERE e.game_id = ?
            ORDER BY e.timestamp ASC
        """, game_id)

        for event in events:
            if event["type"] == "substitution":
                sd = db.execute("""
                    SELECT sd.*,
                           po.name AS player_off_name,
                           pn.name AS player_on_name,
                           t.color AS team_color
                    FROM substitution_details sd
                    JOIN people po ON po.id = sd.player_off_id
                    JOIN people pn ON pn.id = sd.player_on_id
                    JOIN teams  t  ON t.id  = sd.team_id
                    WHERE sd.event_id = ?
                """, event["id"])
                event["substitution"] = sd[0] if sd else None
                event["team_change"]  = None
            elif event["type"] == "team_change":
                tcd = db.execute("""
                    SELECT tcd.*, tl.color AS leaving_color,
                           te.color AS entering_color,
                           ts.color AS staying_color
                    FROM team_change_details tcd
                    JOIN teams tl ON tl.id = tcd.leaving_team_id
                    JOIN teams te ON te.id = tcd.entering_team_id
                    LEFT JOIN teams ts ON ts.id = tcd.staying_team_id
                    WHERE tcd.event_id = ?
                """, event["id"])
                event["team_change"]  = tcd[0] if tcd else None
                event["substitution"] = None
            elif event["type"] == "player_join":
                # Fetch the team the player joined at this timestamp
                jr = db.execute("""
                    SELECT t.color AS team_color, re.is_goalkeeper
                    FROM roster_entries re
                    JOIN teams t ON t.id = re.team_id
                    WHERE re.game_id = ? AND re.person_id = ? AND re.entered_at = ?
                    LIMIT 1
                """, game_id, event["person_id"], event["timestamp"])
                event["player_join"]  = jr[0] if jr else {"team_color": "", "is_goalkeeper": 0}
                event["team_change"]  = None
                event["substitution"] = None
                event["links"]        = []
            elif event["type"] == "player_leave":
                event["player_join"]  = None
                event["team_change"]  = None
                event["substitution"] = None
                event["links"]        = []
            else:
                event["links"] = db.execute("""
                    SELECT el.link_type, pe.name
                    FROM event_links el
                    JOIN people pe ON pe.id = el.linked_person_id
                    WHERE el.event_id = ?
                """, event["id"])
                event["team_change"]  = None
                event["substitution"] = None
                event["player_join"]  = None

        # Active segment
        active_segment = db.execute("""
            SELECT s.*, ta.color AS team_a_color, tb.color AS team_b_color
            FROM segments s
            JOIN teams ta ON ta.id = s.team_a_id
            JOIN teams tb ON tb.id = s.team_b_id
            WHERE s.game_id = ? AND s.ended_at IS NULL
            ORDER BY s.started_at DESC LIMIT 1
        """, game_id)
        active_segment = active_segment[0] if active_segment else None

        youtube_embed = None
        if game["youtube_url"]:
            url = game["youtube_url"]
            if "youtube.com/embed/" in url:
                separator = "&" if "?" in url else "?"
                youtube_embed = url + separator + "enablejsapi=1"
            else:
                video_id = url.split("v=")[-1].split("&")[0]
                youtube_embed = f"https://www.youtube.com/embed/{video_id}?enablejsapi=1"

        # All players who attended (for substitution dropdowns — not just on-field)
        all_players = db.execute("""
            SELECT p.id, p.name, p.nickname
            FROM presences pr
            JOIN people p ON p.id = pr.person_id
            WHERE pr.game_id = ?
            ORDER BY p.name
        """, game_id)

        import json
        roster_timeline = get_roster_timeline(game_id)

        segments = db.execute("""
            SELECT s.id, s.team_a_id, s.team_b_id, s.started_at, s.ended_at,
                   ta.color AS team_a_color, tb.color AS team_b_color
            FROM segments s
            JOIN teams ta ON ta.id = s.team_a_id
            JOIN teams tb ON tb.id = s.team_b_id
            WHERE s.game_id = ?
            ORDER BY s.started_at
        """, game_id)

        # Score each segment (same logic as game_route)
        all_goals = db.execute("""
            SELECT e.timestamp, re.team_id
            FROM events e
            JOIN roster_entries re
              ON re.person_id  = e.person_id
             AND re.game_id    = e.game_id
             AND re.entered_at <= e.timestamp
             AND (re.exited_at IS NULL OR re.exited_at > e.timestamp)
            WHERE e.game_id = ?
              AND e.type    = 'goal'
            ORDER BY e.timestamp
        """, game_id)
        for seg in segments:
            seg_start = seg["started_at"]
            seg_end   = seg["ended_at"] or "99:99:99"
            seg["score_a"] = sum(
                1 for g in all_goals
                if seg_start <= g["timestamp"] <= seg_end
                and int(g["team_id"] or 0) == int(seg["team_a_id"])
            )
            seg["score_b"] = sum(
                1 for g in all_goals
                if seg_start <= g["timestamp"] <= seg_end
                and int(g["team_id"] or 0) == int(seg["team_b_id"])
            )

        # Check if game_start has already been logged
        game_start_event = db.execute(
            "SELECT timestamp FROM events WHERE game_id = ? AND type = 'game_start' LIMIT 1",
            game_id
        )
        game_start_event = game_start_event[0] if game_start_event else None

        steps_done = get_steps_done(db, game_id)
        return render_template("events.html",
            game=game,
            players=players,
            all_players=all_players,
            teams=teams,
            events=events,
            active_segment=active_segment,
            youtube_embed=youtube_embed,
            roster_timeline_json=json.dumps(roster_timeline),
            segments=segments,
            steps_done=steps_done,
            game_start_event=game_start_event,
        )

    # ── POST ─────────────────────────────────────────────────
    event_type = request.form.get("type", "").strip()
    person_id  = request.form.get("person_id", "").strip() or None
    timestamp  = request.form.get("timestamp", "").strip()
    notes      = request.form.get("notes", "").strip() or None
    duration   = request.form.get("duration", "20").strip() or "20"

    if not event_type or not timestamp:
        return jsonify({"success": False, "error": "Type and timestamp are required"}), 400

    # For player_join, person_id comes from the dedicated join_person_id field
    if event_type == "player_join":
        person_id = request.form.get("join_person_id", "").strip() or person_id

    event_id = db.execute("""
        INSERT INTO events (game_id, person_id, type, timestamp, duration, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, game_id, person_id, event_type, timestamp, int(duration), notes)

    response_data = {
        "success": True,
        "event": {
            "id":          event_id,
            "type":        event_type,
            "timestamp":   timestamp,
            "notes":       notes,
            "person_id":   int(person_id) if person_id else None,
            "person_name": None,
            "substitution": None,
            "team_change": None,
            "links":       [],
        }
    }

    if person_id:
        row = db.execute("SELECT name FROM people WHERE id = ?", person_id)
        response_data["event"]["person_name"] = row[0]["name"] if row else None

    # ── Substitution ──────────────────────────────────────────
    if event_type == "substitution":
        player_off_id = request.form.get("player_off_id", "").strip()
        player_on_id  = request.form.get("player_on_id", "").strip()

        if player_off_id and player_on_id:
            is_gk_swap = 1 if request.form.get("is_goalkeeper_swap") else 0

            # Find off-player's current team and GK status
            off_entry = db.execute("""
                SELECT team_id, is_goalkeeper FROM roster_entries
                WHERE game_id = ? AND person_id = ? AND exited_at IS NULL
                ORDER BY entered_at DESC LIMIT 1
            """, game_id, player_off_id)

            # Find on-player's current team and GK status
            on_entry = db.execute("""
                SELECT team_id, is_goalkeeper FROM roster_entries
                WHERE game_id = ? AND person_id = ? AND exited_at IS NULL
                ORDER BY entered_at DESC LIMIT 1
            """, game_id, player_on_id)

            off_team_id = off_entry[0]["team_id"]      if off_entry else None
            off_is_gk   = off_entry[0]["is_goalkeeper"] if off_entry else 0
            on_team_id  = on_entry[0]["team_id"]       if on_entry  else None
            on_is_gk    = on_entry[0]["is_goalkeeper"]  if on_entry  else 0

            # For GK swap: both players stay on same team, only is_goalkeeper flips
            # For regular sub: on-player takes off-player's team
            if is_gk_swap:
                # Both must be on the same team — off loses GK, on gains GK
                new_off_is_gk = 0
                new_on_is_gk  = 1
                new_off_team  = off_team_id  # stays on same team
                new_on_team   = off_team_id  # on-player joins same team
            else:
                new_off_is_gk = off_is_gk
                new_on_is_gk  = on_is_gk
                new_off_team  = on_team_id   # cross-team swap
                new_on_team   = off_team_id

            team_id = off_team_id

            # Store substitution_details
            db.execute("""
                INSERT INTO substitution_details
                    (event_id, player_off_id, player_on_id, team_id, is_goalkeeper_swap)
                VALUES (?, ?, ?, ?, ?)
            """, event_id, player_off_id, player_on_id, team_id or 0, is_gk_swap)

            # Close both players' active roster entries
            db.execute("""
                UPDATE roster_entries SET exited_at = ?
                WHERE game_id = ? AND person_id = ? AND exited_at IS NULL
            """, timestamp, game_id, player_off_id)

            if not is_gk_swap:
                db.execute("""
                    UPDATE roster_entries SET exited_at = ?
                    WHERE game_id = ? AND person_id = ? AND exited_at IS NULL
                """, timestamp, game_id, player_on_id)

            # Open new roster entry for on-player (takes over GK or off-player's team)
            db.execute("""
                INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
                VALUES (?, ?, ?, ?, ?)
            """, game_id, player_on_id, new_on_team, timestamp, new_on_is_gk)

            # Open new entry for off-player (bench or new team)
            if new_off_team:
                db.execute("""
                    INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
                    VALUES (?, ?, ?, ?, ?)
                """, game_id, player_off_id, new_off_team, timestamp, new_off_is_gk)
            # else: off-player goes to bench

            off  = db.execute("SELECT name FROM people WHERE id = ?", player_off_id)
            on   = db.execute("SELECT name FROM people WHERE id = ?", player_on_id)
            team = db.execute("SELECT color FROM teams WHERE id = ?", team_id) if team_id else []
            response_data["event"]["substitution"] = {
                "player_off_name":   off[0]["name"]  if off  else "",
                "player_on_name":    on[0]["name"]   if on   else "",
                "team_color":        team[0]["color"] if team else "",
                "is_goalkeeper_swap": is_gk_swap,
            }
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    # ── Team change ───────────────────────────────────────────
    # ── Game Start ───────────────────────────────────────────
    if event_type == "game_start":
        # Create the first segment starting at this timestamp
        first_segment = None
        if len(teams) >= 2:
            # Close any earlier accidental segment
            db.execute("DELETE FROM segments WHERE game_id = ? AND started_at < ?", game_id, timestamp)
            # Create the real first segment
            existing = db.execute(
                "SELECT id FROM segments WHERE game_id = ? AND started_at = ?", game_id, timestamp
            )
            if not existing:
                db.execute("""
                    INSERT INTO segments (game_id, team_a_id, team_b_id, started_at)
                    VALUES (?, ?, ?, ?)
                """, game_id, teams[0]["id"], teams[1]["id"], timestamp)
            first_segment = {
                "team_a_color": teams[0]["color"],
                "team_b_color": teams[1]["color"],
            }
        response_data["event"]["game_start"] = {"timestamp": timestamp}
        response_data["game_started"] = True
        response_data["first_segment"] = first_segment

    elif event_type == "team_change":
        leaving_team_id  = request.form.get("leaving_team_id", "").strip()
        entering_team_id = request.form.get("entering_team_id", "").strip()

        if leaving_team_id and entering_team_id:
            active_seg = db.execute("""
                SELECT * FROM segments WHERE game_id = ? AND ended_at IS NULL
                ORDER BY started_at DESC LIMIT 1
            """, game_id)

            staying_team_id = None
            if active_seg:
                seg = active_seg[0]
                staying_team_id = (
                    seg["team_b_id"]
                    if str(seg["team_a_id"]) == leaving_team_id
                    else seg["team_a_id"]
                )

            db.execute("""
                INSERT INTO team_change_details
                    (event_id, leaving_team_id, entering_team_id, staying_team_id)
                VALUES (?, ?, ?, ?)
            """, event_id, leaving_team_id, entering_team_id, staying_team_id)

            # Close active segment, open new one
            db.execute("""
                UPDATE segments SET ended_at = ?
                WHERE game_id = ? AND ended_at IS NULL
            """, timestamp, game_id)

            new_a = staying_team_id or leaving_team_id
            new_b = entering_team_id
            db.execute("""
                INSERT INTO segments (game_id, team_a_id, team_b_id, started_at)
                VALUES (?, ?, ?, ?)
            """, game_id, new_a, new_b, timestamp)

            staying  = db.execute("SELECT color FROM teams WHERE id = ?", new_a)
            entering = db.execute("SELECT color FROM teams WHERE id = ?", entering_team_id)
            response_data["event"]["team_change"] = {
                "staying_color":  staying[0]["color"]  if staying  else "",
                "entering_color": entering[0]["color"] if entering else "",
            }
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    # ── Player Join ───────────────────────────────────────────
    elif event_type == "player_join":
        join_team_id   = request.form.get("join_team_id", "").strip() or None
        join_person_id = request.form.get("join_person_id", "").strip() or None
        is_goalkeeper  = 1 if request.form.get("join_is_goalkeeper") else 0
        # Use dedicated join_person_id (attendee list); fall back to person_id
        effective_person_id = join_person_id or person_id
        if effective_person_id and join_team_id:
            # Patch response with correct person info
            prow = db.execute("SELECT name FROM people WHERE id = ?", effective_person_id)
            if prow:
                response_data["event"]["person_id"]   = int(effective_person_id)
                response_data["event"]["person_name"] = prow[0]["name"]
            # Close any existing open entry (guard against duplicates)
            db.execute("""
                UPDATE roster_entries SET exited_at = ?
                WHERE game_id = ? AND person_id = ? AND exited_at IS NULL
            """, timestamp, game_id, effective_person_id)
            # Open new entry
            db.execute("""
                INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
                VALUES (?, ?, ?, ?, ?)
            """, game_id, effective_person_id, join_team_id, timestamp, is_goalkeeper)
            team_row = db.execute("SELECT color FROM teams WHERE id = ?", join_team_id)
            response_data["event"]["player_join"] = {
                "team_color": team_row[0]["color"] if team_row else "",
                "is_goalkeeper": is_goalkeeper,
            }
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    # ── Player Leave ──────────────────────────────────────────
    elif event_type == "player_leave":
        if person_id:
            # Close the active roster entry — stops field time counting
            db.execute("""
                UPDATE roster_entries SET exited_at = ?
                WHERE game_id = ? AND person_id = ? AND exited_at IS NULL
            """, timestamp, game_id, person_id)
            response_data["event"]["player_leave"] = True
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    # ── Other events (goal, highlight, foul, injury) ──────────
    else:
        linked_person_id = request.form.get("linked_person_id", "").strip() or None
        link_type        = request.form.get("link_type", "").strip() or None

        if linked_person_id and link_type:
            db.execute("""
                INSERT INTO event_links (event_id, linked_person_id, link_type)
                VALUES (?, ?, ?)
            """, event_id, linked_person_id, link_type)
            linked = db.execute("SELECT name FROM people WHERE id = ?", linked_person_id)
            response_data["event"]["links"] = [{
                "link_type": link_type,
                "name": linked[0]["name"] if linked else ""
            }]

    return jsonify(response_data)


@app.route("/games/<int:game_id>/events/<int:event_id>", methods=["GET"])
@login_required
@role_required("admin", "editor")
def get_event(game_id, event_id):
    """Get a single event's data for editing."""

    event = db.execute("""
        SELECT e.*, pe.name AS person_name
        FROM events e
        LEFT JOIN people pe ON pe.id = e.person_id
        WHERE e.id = ? AND e.game_id = ?
    """, event_id, game_id)

    if not event:
        return jsonify({"success": False, "error": "Event not found"}), 404

    event = event[0]

    # Get event link if exists
    link = db.execute("""
        SELECT link_type, linked_person_id
        FROM event_links
        WHERE event_id = ?
    """, event_id)

    if link:
        event["link"] = link[0]
    else:
        event["link"] = None

    # Get team change details if exists
    if event["type"] == "team_change":
        team_change = db.execute("""
            SELECT leaving_team_id, entering_team_id, staying_team_id
            FROM team_change_details
            WHERE event_id = ?
        """, event_id)

        if team_change:
            event["team_change_detail"] = team_change[0]
        else:
            event["team_change_detail"] = None

    return jsonify(event)

@app.route("/games/<int:game_id>/events/<int:event_id>", methods=["PUT"])
@login_required
@role_required("admin", "editor")
def update_event(game_id, event_id):
    """Update an existing event."""

    # Verify event exists and belongs to this game
    existing = db.execute("SELECT * FROM events WHERE id = ? AND game_id = ?", event_id, game_id)
    if not existing:
        return jsonify({"success": False, "error": "Event not found"}), 404

    event_type       = request.form.get("type", "").strip()
    person_id        = request.form.get("person_id", "").strip() or None
    timestamp        = request.form.get("timestamp", "").strip()
    notes            = request.form.get("notes", "").strip() or None
    duration         = request.form.get("duration", "20").strip() or "20"
    linked_person_id = request.form.get("linked_person_id", "").strip() or None
    link_type        = request.form.get("link_type", "").strip() or None

    if not event_type:
        return jsonify({"success": False, "error": "Event type is required"}), 400
    if not timestamp:
        return jsonify({"success": False, "error": "Timestamp is required"}), 400

    # Update the event
    db.execute("""
        UPDATE events
        SET type = ?, person_id = ?, timestamp = ?, duration = ?, notes = ?
        WHERE id = ?
    """, event_type, person_id, timestamp, int(duration), notes, event_id)

    # Update event links - delete old and insert new if provided
    db.execute("DELETE FROM event_links WHERE event_id = ?", event_id)
    if linked_person_id and link_type:
        db.execute("""
            INSERT INTO event_links (event_id, linked_person_id, link_type)
            VALUES (?, ?, ?)
        """, event_id, linked_person_id, link_type)

    # Handle team_change updates
    team_change_data = None
    if event_type == "team_change":
        leaving_team_id  = request.form.get("leaving_team_id", "").strip()
        entering_team_id = request.form.get("entering_team_id", "").strip()

        if leaving_team_id and entering_team_id:
            # Delete old team_change_details
            db.execute("DELETE FROM team_change_details WHERE event_id = ?", event_id)

            # Find staying team
            active_seg = db.execute("""
                SELECT * FROM segments WHERE game_id = ? AND ended_at IS NULL
                ORDER BY started_at DESC LIMIT 1
            """, game_id)

            staying_team_id = None
            if active_seg:
                seg = active_seg[0]
                staying_team_id = seg["team_b_id"] if str(seg["team_a_id"]) == leaving_team_id else seg["team_a_id"]

            # Insert new team_change_details
            db.execute("""
                INSERT INTO team_change_details (event_id, leaving_team_id, entering_team_id, staying_team_id)
                VALUES (?, ?, ?, ?)
            """, event_id, leaving_team_id, entering_team_id, staying_team_id)

            # Fetch colors for response
            staying_team = db.execute("SELECT color FROM teams WHERE id = ?", staying_team_id or leaving_team_id)
            entering     = db.execute("SELECT color FROM teams WHERE id = ?", entering_team_id)

            team_change_data = {
                "staying_color":  staying_team[0]["color"] if staying_team else "",
                "entering_color": entering[0]["color"]     if entering     else "",
            }

    # Fetch names for response
    person_name = None
    if person_id:
        row = db.execute("SELECT name FROM people WHERE id = ?", person_id)
        person_name = row[0]["name"] if row else None

    linked_name = None
    if linked_person_id:
        row = db.execute("SELECT name FROM people WHERE id = ?", linked_person_id)
        linked_name = row[0]["name"] if row else None

    return jsonify({
        "success": True,
        "event": {
            "id":           event_id,
            "type":         event_type,
            "timestamp":    timestamp,
            "person_name":  person_name,
            "notes":        notes,
            "link_type":    link_type,
            "linked_name":  linked_name,
            "team_change":  team_change_data,
        }
    })

@app.route("/games/<int:game_id>/events/<int:event_id>", methods=["DELETE"])
@login_required
@role_required("admin", "editor")
def delete_event(game_id, event_id):
    """Delete an event."""
    existing = db.execute("SELECT * FROM events WHERE id = ? AND game_id = ?", event_id, game_id)
    if not existing:
        return jsonify({"success": False, "error": "Event not found"}), 404
    try:
        event_type = existing[0]["type"]

        # ── Revert roster_entries before deleting ──────────────
        if event_type == "substitution":
            sd = db.execute("SELECT * FROM substitution_details WHERE event_id = ?", event_id)
            if sd:
                sd = sd[0]
                ts = existing[0]["timestamp"]
                # Delete the new entries that were created by this substitution
                db.execute("""
                    DELETE FROM roster_entries
                    WHERE game_id = ? AND entered_at = ?
                      AND person_id IN (?, ?)
                """, game_id, ts, sd["player_off_id"], sd["player_on_id"])
                # Reopen the entries that were closed by this substitution
                db.execute("""
                    UPDATE roster_entries SET exited_at = NULL
                    WHERE game_id = ? AND exited_at = ?
                      AND person_id IN (?, ?)
                """, game_id, ts, sd["player_off_id"], sd["player_on_id"])

        elif event_type == "player_join":
            ts = existing[0]["timestamp"]
            pid = existing[0]["person_id"]
            # Remove the roster entry created by this join
            db.execute("""
                DELETE FROM roster_entries
                WHERE game_id = ? AND person_id = ? AND entered_at = ?
            """, game_id, pid, ts)

        elif event_type == "player_leave":
            ts = existing[0]["timestamp"]
            pid = existing[0]["person_id"]
            # Reopen the entry that was closed by this leave
            db.execute("""
                UPDATE roster_entries SET exited_at = NULL
                WHERE game_id = ? AND person_id = ? AND exited_at = ?
            """, game_id, pid, ts)

        elif event_type == "game_start":
            ts = existing[0]["timestamp"]
            # Remove the segment that was created by game_start
            db.execute("""
                DELETE FROM segments WHERE game_id = ? AND started_at = ?
            """, game_id, ts)

        # ── Delete event and related records ───────────────────
        db.execute("DELETE FROM event_links WHERE event_id = ?", event_id)
        db.execute("DELETE FROM team_change_details WHERE event_id = ?", event_id)
        db.execute("DELETE FROM substitution_details WHERE event_id = ?", event_id)
        db.execute("DELETE FROM events WHERE id = ?", event_id)

        recompute_player_stats()

        roster_affecting = {"substitution", "team_change", "player_join", "player_leave"}
        response = {"success": True}
        if event_type in roster_affecting:
            recompute_roster_snapshots(game_id)
            response["roster_timeline"] = get_roster_timeline(game_id)

        return jsonify(response)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/leaderboard")
@login_required
def leaderboard():
    """Show player rankings with optional filters by game or time period."""

    # ── Filter params ─────────────────────────────────────────
    filter_type = request.args.get("filter", "all")   # all | game | timeframe
    game_id     = request.args.get("game_id", "")
    timeframe   = request.args.get("timeframe", "")   # YYYY | YYYY-Q1..Q4

    # All games for the filter dropdown
    all_games = db.execute("""
        SELECT id, title, date FROM games ORDER BY date DESC
    """)

    # Build WHERE clause for the time/game filter
    where_clauses = []
    if filter_type == "game" and game_id:
        where_clauses.append(f"g.id = {int(game_id)}")
    elif filter_type == "timeframe" and timeframe:
        if "-Q" in timeframe:
            year, q = timeframe.split("-Q")
            q = int(q)
            month_start = (q - 1) * 3 + 1
            month_end   = month_start + 2
            where_clauses.append(
                f"strftime('%Y', g.date) = '{year}' AND "
                f"CAST(strftime('%m', g.date) AS INT) BETWEEN {month_start} AND {month_end}"
            )
        else:
            where_clauses.append(f"strftime('%Y', g.date) = '{timeframe}'")

    game_where = ("AND " + " AND ".join(where_clauses)) if where_clauses else ""

    # When filtering by game/timeframe, compute time on-the-fly for that subset.
    # For "all time" (no filter), read from player_stats_cache — no calculation needed.
    if game_where:
        # Filtered: calculate time for the specific subset
        game_end_expr = _secs('NULL', fallback_col='g.video_end')
        time_subquery = f"""
            LEFT JOIN (
                SELECT
                    person_id,
                    MIN(SUM_field, game_end) AS field_seconds,
                    MIN(SUM_gk,    game_end) AS gk_seconds
                FROM (
                    SELECT
                        re.person_id,
                        {game_end_expr} AS game_end,
                        SUM(CASE WHEN re.team_id IS NOT NULL THEN
                            MAX(0, MIN(
                                {_secs('re.exited_at')}  - {_secs_entered('re.entered_at')},
                                {game_end_expr}          - {_secs_entered('re.entered_at')}
                            ))
                        END) AS SUM_field,
                        SUM(CASE WHEN re.is_goalkeeper = 1 THEN
                            MAX(0, MIN(
                                {_secs('re.exited_at')}  - {_secs_entered('re.entered_at')},
                                {game_end_expr}          - {_secs_entered('re.entered_at')}
                            ))
                        END) AS SUM_gk
                    FROM roster_entries re
                    JOIN games g ON g.id = re.game_id
                    WHERE 1=1 {game_where}
                    GROUP BY re.person_id, g.id
                )
                GROUP BY person_id
            ) t ON t.person_id = p.id
        """
        # Late time subquery for filtered view
        late_subquery = f"""
            LEFT JOIN (
                SELECT
                    pr.person_id,
                    SUM(CASE
                        WHEN pr.arrived_at IS NOT NULL AND (
                            (CAST(substr(pr.arrived_at,1,2) AS INT)*3600 +
                             CAST(substr(pr.arrived_at,4,2) AS INT)*60 +
                             CAST(substr(pr.arrived_at,7,2) AS INT))
                            >
                            (CAST(substr(COALESCE(g.video_start,'00:00:00'),1,2) AS INT)*3600 +
                             CAST(substr(COALESCE(g.video_start,'00:00:00'),4,2) AS INT)*60 +
                             CAST(substr(COALESCE(g.video_start,'00:00:00'),7,2) AS INT))
                        ) THEN
                            (CAST(substr(pr.arrived_at,1,2) AS INT)*3600 +
                             CAST(substr(pr.arrived_at,4,2) AS INT)*60 +
                             CAST(substr(pr.arrived_at,7,2) AS INT))
                            -
                            (CAST(substr(COALESCE(g.video_start,'00:00:00'),1,2) AS INT)*3600 +
                             CAST(substr(COALESCE(g.video_start,'00:00:00'),4,2) AS INT)*60 +
                             CAST(substr(COALESCE(g.video_start,'00:00:00'),7,2) AS INT))
                        ELSE 0
                    END) AS late_seconds,
                    SUM(CASE
                        WHEN pr.arrived_at IS NOT NULL AND (
                            (CAST(substr(pr.arrived_at,1,2) AS INT)*3600 +
                             CAST(substr(pr.arrived_at,4,2) AS INT)*60 +
                             CAST(substr(pr.arrived_at,7,2) AS INT))
                            >
                            (CAST(substr(COALESCE(g.video_start,'00:00:00'),1,2) AS INT)*3600 +
                             CAST(substr(COALESCE(g.video_start,'00:00:00'),4,2) AS INT)*60 +
                             CAST(substr(COALESCE(g.video_start,'00:00:00'),7,2) AS INT))
                        ) THEN 1
                        ELSE 0
                    END) AS late_arrivals
                FROM presences pr
                JOIN games g ON g.id = pr.game_id
                WHERE 1=1 {game_where}
                GROUP BY pr.person_id
            ) lt ON lt.person_id = p.id
        """
    else:
        # All time: use pre-computed cache — fast
        time_subquery = """
            LEFT JOIN player_stats_cache t ON t.person_id = p.id
        """
        late_subquery = """
            LEFT JOIN player_stats_cache lt ON lt.person_id = p.id
        """

    # Bench subquery: available game seconds per player = video_end minus arrived_at (or 0 if from start)
    bench_subquery = f"""
        LEFT JOIN (
            SELECT pr.person_id,
                SUM(
                    -- total game seconds available to this player (from their arrival to video_end)
                    (CAST(substr(COALESCE(g.video_end, '01:30:00'),1,2) AS INT)*3600 +
                     CAST(substr(COALESCE(g.video_end, '01:30:00'),4,2) AS INT)*60 +
                     CAST(substr(COALESCE(g.video_end, '01:30:00'),7,2) AS INT))
                    -
                    (CAST(substr(COALESCE(pr.arrived_at, '00:00:00'),1,2) AS INT)*3600 +
                     CAST(substr(COALESCE(pr.arrived_at, '00:00:00'),4,2) AS INT)*60 +
                     CAST(substr(COALESCE(pr.arrived_at, '00:00:00'),7,2) AS INT))
                ) AS total_game_seconds
            FROM presences pr
            JOIN games g ON g.id = pr.game_id
            WHERE 1=1 {game_where}
            GROUP BY pr.person_id
        ) b ON b.person_id = p.id
    """

    players = db.execute(f"""
        SELECT
            p.id,
            p.name,
            p.nickname,
            COUNT(DISTINCT CASE WHEN e.type = 'goal'         THEN e.id  END) AS goals,
            COUNT(DISTINCT CASE WHEN el.link_type = 'assist' THEN el.id END) AS assists,
            COUNT(DISTINCT pr.game_id) AS appearances,
            COALESCE(t.field_seconds, 0)  AS field_seconds,
            COALESCE(t.gk_seconds,    0)  AS gk_seconds,
            MAX(0, COALESCE(b.total_game_seconds, 0)
                   - COALESCE(t.field_seconds, 0)) AS bench_seconds,
            COALESCE(lt.late_seconds,  0) AS late_seconds,
            COALESCE(lt.late_arrivals, 0) AS late_arrivals
        FROM people p
        LEFT JOIN events e          ON e.person_id         = p.id
                                    AND e.game_id IN (SELECT id FROM games g WHERE 1=1 {game_where})
        LEFT JOIN event_links el    ON el.linked_person_id = p.id
                                    AND el.event_id IN (SELECT id FROM events WHERE game_id IN
                                        (SELECT id FROM games g WHERE 1=1 {game_where}))
        LEFT JOIN presences pr      ON pr.person_id        = p.id
                                    AND pr.game_id IN (SELECT id FROM games g WHERE 1=1 {game_where})
        {time_subquery}
        {bench_subquery}
        {late_subquery}
        GROUP BY p.id
        HAVING appearances > 0
        ORDER BY goals DESC, assists DESC, appearances DESC
    """)

    # Build year/quarter options from actual game dates
    years = sorted(set(
        g["date"][:4] for g in all_games if g["date"]
    ), reverse=True)
    timeframe_options = []
    for y in years:
        timeframe_options.append({"value": y, "label": y})
        for q, label in [("Q1","Jan–Mar"),("Q2","Apr–Jun"),("Q3","Jul–Sep"),("Q4","Oct–Dec")]:
            timeframe_options.append({"value": f"{y}-{q}", "label": f"{y} {q} ({label})"})

    return render_template("leaderboard.html",
        players=players,
        all_games=all_games,
        timeframe_options=timeframe_options,
        filter_type=filter_type,
        active_game_id=game_id,
        active_timeframe=timeframe,
    )


@app.route("/player/<int:person_id>")
@login_required
def player(person_id):
    """Show a player's profile and event history."""

    person = db.execute("SELECT * FROM people WHERE id = ?", person_id)
    if not person:
        return apology("Player not found", 404)
    person = person[0]

    # Stats
    stats = db.execute("""
        SELECT
            COUNT(DISTINCT CASE WHEN e.type = 'goal'         THEN e.id  END) AS goals,
            COUNT(DISTINCT CASE WHEN el.link_type = 'assist' THEN el.id END) AS assists,
            COUNT(DISTINCT pr.game_id) AS appearances
        FROM people p
        LEFT JOIN events e       ON e.person_id         = p.id
        LEFT JOIN event_links el ON el.linked_person_id = p.id
        LEFT JOIN presences pr   ON pr.person_id        = p.id
        WHERE p.id = ?
    """, person_id)[0]

    # All events involving this player
    events = db.execute("""
        SELECT e.*, g.title AS game_title, g.date AS game_date, g.youtube_url
        FROM events e
        JOIN games g ON g.id = e.game_id
        WHERE e.person_id = ?
        ORDER BY g.date DESC, e.timestamp ASC
    """, person_id)

    def build_embed_url(raw_url):
        if not raw_url:
            return None
        if "youtube.com/embed/" in raw_url:
            sep = "&" if "?" in raw_url else "?"
            return raw_url + sep + "enablejsapi=1"
        if "youtu.be/" in raw_url:
            vid = raw_url.split("youtu.be/")[1].split("?")[0]
        elif "v=" in raw_url:
            vid = raw_url.split("v=")[1].split("&")[0]
        else:
            return None
        return f"https://www.youtube.com/embed/{vid}?enablejsapi=1"

    for event in events:
        event["links"] = db.execute("""
            SELECT el.link_type, pe.name
            FROM event_links el
            JOIN people pe ON pe.id = el.linked_person_id
            WHERE el.event_id = ?
        """, event["id"])
        event["youtube_embed"] = build_embed_url(event.get("youtube_url"))

    # Games played
    games = db.execute("""
        SELECT g.id, g.title, g.date, l.name AS location
        FROM presences pr
        JOIN games g     ON g.id  = pr.game_id
        JOIN locations l ON l.id  = g.location_id
        WHERE pr.person_id = ?
        ORDER BY g.date DESC
    """, person_id)

    # ── Time stats — read from cache (recomputed on roster/sub changes) ─────────
    cache = db.execute("""
        SELECT field_seconds, gk_seconds, late_seconds, late_arrivals
        FROM player_stats_cache WHERE person_id = ?
    """, person_id)
    field_seconds  = cache[0]["field_seconds"]  if cache else 0
    gk_seconds     = cache[0]["gk_seconds"]     if cache else 0
    late_seconds   = cache[0]["late_seconds"]   if cache else 0
    late_arrivals  = cache[0]["late_arrivals"]  if cache else 0

    # Bench time: total available game seconds (from arrived_at to video_end) minus field time
    bench_time = db.execute("""
        SELECT SUM(
            (CAST(substr(COALESCE(g.video_end, '01:30:00'),1,2) AS INT)*3600 +
             CAST(substr(COALESCE(g.video_end, '01:30:00'),4,2) AS INT)*60 +
             CAST(substr(COALESCE(g.video_end, '01:30:00'),7,2) AS INT))
            -
            (CAST(substr(COALESCE(pr.arrived_at, '00:00:00'),1,2) AS INT)*3600 +
             CAST(substr(COALESCE(pr.arrived_at, '00:00:00'),4,2) AS INT)*60 +
             CAST(substr(COALESCE(pr.arrived_at, '00:00:00'),7,2) AS INT))
        ) AS total_game_seconds
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = ?
    """, person_id)
    total_game_seconds = bench_time[0]["total_game_seconds"] or 0 if bench_time else 0
    bench_seconds = max(0, total_game_seconds - field_seconds)

    # Per-game late time — how late each game
    late_by_game = db.execute("""
        SELECT
            pr.game_id,
            pr.arrived_at,
            g.video_start,
            CASE
                WHEN pr.arrived_at IS NOT NULL AND (
                    (CAST(substr(pr.arrived_at,1,2) AS INT)*3600 +
                     CAST(substr(pr.arrived_at,4,2) AS INT)*60 +
                     CAST(substr(pr.arrived_at,7,2) AS INT))
                    >
                    (CAST(substr(COALESCE(g.video_start,'00:00:00'),1,2) AS INT)*3600 +
                     CAST(substr(COALESCE(g.video_start,'00:00:00'),4,2) AS INT)*60 +
                     CAST(substr(COALESCE(g.video_start,'00:00:00'),7,2) AS INT))
                ) THEN
                    (CAST(substr(pr.arrived_at,1,2) AS INT)*3600 +
                     CAST(substr(pr.arrived_at,4,2) AS INT)*60 +
                     CAST(substr(pr.arrived_at,7,2) AS INT))
                    -
                    (CAST(substr(COALESCE(g.video_start,'00:00:00'),1,2) AS INT)*3600 +
                     CAST(substr(COALESCE(g.video_start,'00:00:00'),4,2) AS INT)*60 +
                     CAST(substr(COALESCE(g.video_start,'00:00:00'),7,2) AS INT))
                ELSE NULL
            END AS late_secs
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = ?
    """, person_id)
    late_by_game_map = {r["game_id"]: r["late_secs"] for r in late_by_game}

    # Per-game GK time — closed entries only; open entries (still GK at game end)
    # are handled separately and summed together to avoid the NULL fallback bug
    gk_by_game = db.execute(f"""
        SELECT game_id, title, date, SUM(gk_seconds) AS gk_seconds
        FROM (
            SELECT g.id AS game_id, g.title, g.date,
                {_secs('re.exited_at')} - {_secs_entered('re.entered_at')} AS gk_seconds
            FROM roster_entries re
            JOIN games g ON g.id = re.game_id
            WHERE re.person_id = ? AND re.is_goalkeeper = 1
        )
        GROUP BY game_id
        ORDER BY date DESC
    """, person_id)

    # Most recent game with video for the player profile panel
    recent_video = db.execute("""
        SELECT g.id, g.title, g.date, g.youtube_url
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = ? AND g.youtube_url IS NOT NULL AND g.youtube_url != ''
        ORDER BY g.date DESC LIMIT 1
    """, person_id)
    recent_video = recent_video[0] if recent_video else None
    player_youtube_embed = build_embed_url(recent_video["youtube_url"]) if recent_video else None

    return render_template("player.html",
        person=person,
        stats=stats,
        events=events,
        games=games,
        gk_seconds=gk_seconds,
        gk_by_game=gk_by_game,
        field_seconds=field_seconds,
        bench_seconds=bench_seconds,
        late_seconds=late_seconds,
        late_arrivals=late_arrivals,
        late_by_game_map=late_by_game_map,
        recent_video=recent_video,
        player_youtube_embed=player_youtube_embed,
    )

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """User profile — link account to a player."""

    user = db.execute("SELECT * FROM users WHERE id = ?", session["user_id"])[0]

    # Current linked player if any
    linked_player = None
    if user["person_id"]:
        row = db.execute("SELECT * FROM people WHERE id = ?", user["person_id"])
        if row:
            linked_player = row[0]

    # All players not yet linked to a user account
    available_players = db.execute("""
        SELECT p.id, p.name, p.nickname
        FROM people p
        LEFT JOIN users u ON u.person_id = p.id
        WHERE u.person_id IS NULL
        ORDER BY p.name
    """)

    if request.method == "GET":
        return render_template("profile.html",
            user=user,
            linked_player=linked_player,
            available_players=available_players
        )

    action = request.form.get("action")

    # Link to a player
    if action == "link":
        person_id = request.form.get("person_id", "").strip()
        if not person_id:
            return apology("Please select a player")

        # Make sure no one else is linked to this player
        conflict = db.execute(
            "SELECT id FROM users WHERE person_id = ?", person_id
        )
        if conflict:
            return apology("This player is already linked to another account")

        db.execute(
            "UPDATE users SET person_id = ? WHERE id = ?",
            person_id, session["user_id"]
        )
        session["person_id"] = int(person_id)
        flash("Player profile linked successfully!")

    # Unlink
    elif action == "unlink":
        db.execute(
            "UPDATE users SET person_id = NULL WHERE id = ?",
            session["user_id"]
        )
        session["person_id"] = None
        flash("Player profile unlinked.")

    # Change password
    elif action == "password":
        current  = request.form.get("current_password", "")
        new_pass = request.form.get("new_password", "")
        confirm  = request.form.get("confirm_password", "")

        if not check_password_hash(user["hash"], current):
            return apology("Current password is incorrect")
        if not new_pass:
            return apology("New password cannot be empty")
        if new_pass != confirm:
            return apology("Passwords do not match")

        db.execute(
            "UPDATE users SET hash = ? WHERE id = ?",
            generate_password_hash(new_pass), session["user_id"]
        )
        flash("Password changed successfully!")

    return redirect("/profile")


@app.route("/admin")
@login_required
@role_required("admin", "editor")
def admin():
    """Admin dashboard."""
    people_count    = db.execute("SELECT COUNT(*) AS n FROM people")[0]["n"]
    games_count     = db.execute("SELECT COUNT(*) AS n FROM games")[0]["n"]
    locations_count = db.execute("SELECT COUNT(*) AS n FROM locations")[0]["n"]
    users_count     = db.execute("SELECT COUNT(*) AS n FROM users")[0]["n"]
    return render_template("admin/index.html",
        people_count=people_count,
        games_count=games_count,
        locations_count=locations_count,
        users_count=users_count
    )


# ── People ────────────────────────────────────────────────────

@app.route("/admin/people")
@login_required
@role_required("admin", "editor")
def admin_people():
    people = db.execute("""
        SELECT p.*, g.name AS guest_of_name, g.nickname AS guest_of_nickname
        FROM people p
        LEFT JOIN people g ON g.id = p.guest_of
        ORDER BY p.name
    """)
    all_people = db.execute("SELECT id, name, nickname FROM people ORDER BY name")
    return render_template("admin/people.html", people=people, all_people=all_people)


@app.route("/admin/people/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def admin_people_add():
    if request.method == "GET":
        all_people = db.execute("SELECT id, name, nickname FROM people ORDER BY name")
        return render_template("admin/people_form.html", person=None, all_people=all_people)

    name         = request.form.get("name", "").strip()
    nickname     = request.form.get("nickname", "").strip() or None
    phone_number = request.form.get("phone_number", "").strip() or None

    if not name:
        return apology("Name is required")

    try:
        new_id = db.execute("""
            INSERT INTO people (name, nickname, phone_number)
            VALUES (?, ?, ?)
        """, name, nickname, phone_number)
    except ValueError:
        return apology("Phone number already exists")

    flash(f"{name} added. You can now upload a photo.")
    return redirect(f"/admin/people/{new_id}/edit")


@app.route("/admin/people/<int:person_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def admin_people_edit(person_id):
    person = db.execute("""
        SELECT p.*, g.name AS guest_of_name, g.nickname AS guest_of_nickname
        FROM people p
        LEFT JOIN people g ON g.id = p.guest_of
        WHERE p.id = ?
    """, person_id)
    if not person:
        return apology("Player not found", 404)
    person = person[0]

    if request.method == "GET":
        all_people = db.execute("SELECT id, name, nickname FROM people WHERE id != ? ORDER BY name", person_id)
        return render_template("admin/people_form.html", person=person, all_people=all_people)

    name             = request.form.get("name", "").strip()
    nickname         = request.form.get("nickname", "").strip() or None
    phone_number     = request.form.get("phone_number", "").strip() or None
    is_in_group_chat = 1 if request.form.get("is_in_group_chat") else 0
    is_guest         = 1 if request.form.get("is_guest") else 0
    guest_of_raw     = request.form.get("guest_of", "").strip()
    guest_of         = int(guest_of_raw) if is_guest and guest_of_raw.isdigit() else None

    if not name:
        return apology("Name is required")

    try:
        db.execute("""
            UPDATE people
            SET name = ?, nickname = ?, phone_number = ?,
                is_in_group_chat = ?, is_guest = ?, guest_of = ?,
                updated_at = datetime('now')
            WHERE id = ?
        """, name, nickname, phone_number, is_in_group_chat, is_guest, guest_of, person_id)
    except ValueError:
        return apology("Phone number already exists")

    flash(f"{name} updated successfully!")
    return redirect("/admin/people")

@app.route("/admin/people/<int:person_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_people_delete(person_id):
    person = db.execute("SELECT * FROM people WHERE id = ?", person_id)
    if not person:
        return apology("Player not found", 404)

    db.execute("DELETE FROM people WHERE id = ?", person_id)
    flash("Player deleted.")
    return redirect("/admin/people")

@app.route("/admin/people/<int:person_id>/flags", methods=["POST"])
@login_required
@role_required("admin", "editor")
def admin_people_flags(person_id):
    """Update is_in_group_chat, is_guest, guest_of for a player."""
    is_in_group_chat = 1 if request.form.get("is_in_group_chat") else 0
    is_guest         = 1 if request.form.get("is_guest") else 0
    guest_of_raw     = request.form.get("guest_of", "").strip()
    guest_of         = int(guest_of_raw) if is_guest and guest_of_raw.isdigit() else None
    db.execute("""
        UPDATE people
        SET is_in_group_chat = ?,
            is_guest         = ?,
            guest_of         = ?,
            updated_at       = datetime('now')
        WHERE id = ?
    """, is_in_group_chat, is_guest, guest_of, person_id)
    flash("Player flags updated.")
    return redirect("/admin/people")

# ── Locations ─────────────────────────────────────────────────

@app.route("/admin/locations")
@login_required
@role_required("admin", "editor")
def admin_locations():
    locations = db.execute("""
        SELECT l.*, COUNT(g.id) AS game_count
        FROM locations l
        LEFT JOIN games g ON g.location_id = l.id
        GROUP BY l.id
        ORDER BY l.name
    """)
    return render_template("admin/locations.html", locations=locations)


@app.route("/admin/locations/add", methods=["POST"])
@login_required
@role_required("admin", "editor")
def admin_locations_add():
    name = request.form.get("name", "").strip()
    if not name:
        return apology("Location name is required")
    try:
        db.execute("INSERT INTO locations (name) VALUES (?)", name)
        flash(f"'{name}' added.")
    except ValueError:
        flash("Location already exists.")
    return redirect("/admin/locations")


@app.route("/admin/locations/<int:location_id>/edit", methods=["POST"])
@login_required
@role_required("admin", "editor")
def admin_locations_edit(location_id):
    name = request.form.get("name", "").strip()
    if not name:
        return apology("Location name is required")
    try:
        db.execute("UPDATE locations SET name = ? WHERE id = ?", name, location_id)
        flash(f"Location updated to '{name}'.")
    except ValueError:
        flash("Location name already exists.")
    return redirect("/admin/locations")


@app.route("/admin/locations/<int:location_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_locations_delete(location_id):
    try:
        db.execute("DELETE FROM locations WHERE id = ?", location_id)
        flash("Location deleted.")
    except Exception:
        flash("Cannot delete — location is used by one or more games.")
    return redirect("/admin/locations")


# ── Games management ──────────────────────────────────────────

@app.route("/admin/games")
@login_required
@role_required("admin", "editor")
def admin_games():
    games = db.execute("""
        SELECT g.id, g.title, g.date, l.name AS location,
               COUNT(DISTINCT pr.person_id) AS player_count,
               COUNT(DISTINCT e.id) AS event_count
        FROM games g
        JOIN locations l ON l.id = g.location_id
        LEFT JOIN presences pr ON pr.game_id = g.id
        LEFT JOIN events e ON e.game_id = g.id
        GROUP BY g.id
        ORDER BY g.date DESC
    """)
    return render_template("admin/games.html", games=games)


@app.route("/admin/games/<int:game_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def admin_games_edit(game_id):
    game = db.execute("SELECT * FROM games WHERE id = ?", game_id)
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    locations = db.execute("SELECT * FROM locations ORDER BY name")

    if request.method == "GET":
        return render_template("admin/game_form.html", game=game, locations=locations)

    title       = request.form.get("title", "").strip()
    date        = request.form.get("date", "").strip()
    location_id = request.form.get("location_id", "").strip()
    youtube     = request.form.get("youtube_url", "").strip() or None
    vid_start   = request.form.get("video_start", "").strip() or None
    vid_end     = request.form.get("video_end", "").strip() or None

    if not title or not date or not location_id:
        return apology("Title, date and location are required")

    db.execute("""
        UPDATE games SET title = ?, date = ?, location_id = ?,
                         youtube_url = ?, video_start = ?, video_end = ?
        WHERE id = ?
    """, title, date, location_id, youtube, vid_start, vid_end, game_id)

    flash("Game updated successfully!")
    return redirect("/admin/games")


@app.route("/admin/games/<int:game_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_games_delete(game_id):
    db.execute("DELETE FROM games WHERE id = ?", game_id)
    flash("Game deleted.")
    return redirect("/admin/games")


# ── Users management (admin only) ─────────────────────────────

@app.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    users = db.execute("""
        SELECT u.*, p.name AS player_name
        FROM users u
        LEFT JOIN people p ON p.id = u.person_id
        ORDER BY u.username
    """)
    return render_template("admin/users.html", users=users)

@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
@role_required("admin")
def admin_users_role(user_id):
    role = request.form.get("role", "").strip()
    if role not in ["admin", "editor", "viewer"]:
        return apology("Invalid role")
    if user_id == session["user_id"]:
        return apology("You cannot change your own role")
    db.execute("UPDATE users SET role = ? WHERE id = ?", role, user_id)
    flash("Role updated.")
    return redirect("/admin/users")

# ── Photo management ─────────────────────────────

@app.route("/admin/people/<int:person_id>/photo", methods=["POST"])
@login_required
@role_required("admin")
def admin_upload_photo(person_id):
    """Admin: upload a player photo."""
    person = db.execute("SELECT id FROM people WHERE id = ?", person_id)
    if not person:
        return apology("Player not found", 404)

    file = request.files.get("photo")
    if not file or file.filename == "":
        flash("No file selected.")
        return redirect(f"/admin/people/{person_id}/edit")
    if not allowed_file(file.filename):
        flash("Only JPG, PNG, or WEBP files are allowed.")
        return redirect(f"/admin/people/{person_id}/edit")

    save_photo(person_id, file)
    flash("Photo updated.")
    return redirect(f"/admin/people/{person_id}/edit")


@app.route("/profile/photo", methods=["POST"])
@login_required
def profile_upload_photo():
    """User: upload their own player photo."""
    person_id = session.get("person_id")
    if not person_id:
        flash("Link your account to a player first.")
        return redirect("/profile")

    file = request.files.get("photo")
    if not file or file.filename == "":
        flash("No file selected.")
        return redirect("/profile")
    if not allowed_file(file.filename):
        flash("Only JPG, PNG, or WEBP files are allowed.")
        return redirect("/profile")

    save_photo(person_id, file)
    flash("Photo updated.")
    return redirect("/profile")

@app.route("/recompute")
@login_required
def recompute():
    recompute_player_stats()
    return redirect("/")


@app.route("/recomputeroster/<int:game_id>")
@login_required
def recomputeroster(game_id):
    recompute_roster_snapshots(game_id)
    return redirect("/")

@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()

    if len(q) < 2:
        return render_template("search.html", q=q, results=None, too_short=(len(q) > 0))

    like = f"%{q}%"
    cap  = 20

    # ── Players ──────────────────────────────────────────────
    players = db.execute("""
        SELECT id, name, nickname
        FROM people
        WHERE name LIKE ? OR nickname LIKE ?
        ORDER BY name
        LIMIT ?
    """, like, like, cap)

    # ── Games ─────────────────────────────────────────────────
    games = db.execute("""
        SELECT g.id, g.title, g.date, l.name AS location
        FROM games g
        JOIN locations l ON l.id = g.location_id
        WHERE g.title LIKE ? OR g.date LIKE ? OR l.name LIKE ?
        ORDER BY g.date DESC
        LIMIT ?
    """, like, like, like, cap)

    # ── Events ───────────────────────────────────────────────
    # Searches across:
    #   - the primary person (scorer, injured, highlighted, carded, etc.)
    #   - linked people (assist, foul_on, featured)
    #   - sub players (player_on, player_off)
    #   - event notes
    #   - game title / date
    # Returns one row per event, with enough context to render a useful result.

    events = db.execute("""
        SELECT
            e.id,
            e.type,
            e.timestamp,
            e.notes,
            g.id    AS game_id,
            g.title AS game_title,
            g.date  AS game_date,
            -- primary person
            pp.id       AS person_id,
            pp.name     AS person_name,
            pp.nickname AS person_nickname,
            -- linked person (first match, e.g. assist / foul_on / featured)
            lp.id         AS linked_person_id,
            lp.name       AS linked_person_name,
            lp.nickname   AS linked_person_nickname,
            el.link_type,
            -- substitution: player on / off
            pon.id       AS player_on_id,
            pon.name     AS player_on_name,
            pon.nickname AS player_on_nickname,
            poff.id      AS player_off_id,
            poff.name    AS player_off_name,
            poff.nickname AS player_off_nickname
        FROM events e
        JOIN games g ON g.id = e.game_id
        LEFT JOIN people pp   ON pp.id  = e.person_id
        LEFT JOIN event_links el  ON el.event_id = e.id
        LEFT JOIN people lp   ON lp.id  = el.linked_person_id
        LEFT JOIN substitution_details sd  ON sd.event_id = e.id
        LEFT JOIN people pon  ON pon.id  = sd.player_on_id
        LEFT JOIN people poff ON poff.id = sd.player_off_id
        WHERE
            pp.name    LIKE ? OR pp.nickname    LIKE ?
            OR lp.name LIKE ? OR lp.nickname    LIKE ?
            OR pon.name LIKE ? OR pon.nickname  LIKE ?
            OR poff.name LIKE ? OR poff.nickname LIKE ?
            OR e.notes LIKE ?
            OR g.title LIKE ? OR g.date LIKE ?
        GROUP BY e.id
        ORDER BY g.date DESC, e.timestamp ASC
        LIMIT ?
    """, like, like, like, like, like, like, like, like, like, like, like, cap)

    results = {
        "players": players,
        "games":   games,
        "events":  events,
    }
    total = len(players) + len(games) + len(events)

    return render_template("search.html", q=q, results=results, total=total, too_short=False)
