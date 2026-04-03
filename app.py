import os
import re
from itertools import groupby
from collections import OrderedDict, defaultdict

from flask import Flask, flash, redirect, render_template, request, session, jsonify
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash
from PIL import Image
from dotenv import load_dotenv

from helpers import apology, login_required, role_required, dateformat

load_dotenv()

# Configure application
app = Flask(__name__)

# Custom filter
app.jinja_env.filters["dateformat"] = dateformat

# Secret key
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

# Database
uri = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/arnicafq")
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "connect_args": {"sslmode": "require"},
    "pool_pre_ping": True  # Helps recover from dropped connections
}

db = SQLAlchemy(app)

# Configure session to use SQLAlchemy
app.config["SESSION_TYPE"] = "sqlalchemy"
app.config["SESSION_PERMANENT"] = True
app.config["SESSION_SQLALCHEMY"] = db
app.config["SESSION_SQLALCHEMY_TABLE"] = "sessions"
Session(app)

UPLOAD_FOLDER = os.path.join("static", "uploads", "players")
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
PHOTO_SIZE = (256, 256)  # square crop


# ── DB helper ─────────────────────────────────────────────────────────────────
def query(sql, params=None):
    """Execute a SQL query and return results as a list of dicts."""
    with db.engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        if result.returns_rows:
            keys = result.keys()
            return [dict(zip(keys, row)) for row in result.fetchall()]
        return []

def execute(sql, params=None):
    """Execute a SQL statement (INSERT/UPDATE/DELETE). Returns lastrowid for INSERTs."""
    with db.engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        if result.lastrowid:
            return result.lastrowid
        # For PostgreSQL RETURNING
        try:
            row = result.fetchone()
            if row:
                return row[0]
        except Exception:
            pass
        return None


# ── Photo helpers ──────────────────────────────────────────────────────────────
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

    for ext in ["jpg", "png", "webp"]:
        old = os.path.join(UPLOAD_FOLDER, f"{person_id}.{ext}")
        if os.path.exists(old):
            os.remove(old)

    dest = os.path.join(UPLOAD_FOLDER, f"{person_id}.jpg")
    img = Image.open(file)

    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    img  = img.crop((left, top, left + side, top + side))
    img  = img.resize(PHOTO_SIZE, Image.LANCZOS)
    img.save(dest, "JPEG", quality=85)

    return f"/static/uploads/players/{person_id}.jpg"


# ── Time helpers ───────────────────────────────────────────────────────────────
def _secs(col, fallback_col="g.video_end", hard_default="01:30:00"):
    """PostgreSQL-compatible time-to-seconds expression."""
    eff = f"COALESCE({col}, {fallback_col}, '{hard_default}')"
    return (
        f"(CAST(substring({eff} FROM 1 FOR 2) AS INT)*3600 +"
        f" CAST(substring({eff} FROM 4 FOR 2) AS INT)*60 +"
        f" CAST(substring({eff} FROM 7 FOR 2) AS INT))"
    )

def _secs_entered(col):
    """PostgreSQL-compatible time-to-seconds expression (no fallback)."""
    return (
        f"(CAST(substring({col} FROM 1 FOR 2) AS INT)*3600 +"
        f" CAST(substring({col} FROM 4 FOR 2) AS INT)*60 +"
        f" CAST(substring({col} FROM 7 FOR 2) AS INT))"
    )

def _pg_time_secs(col, fallback="'00:00:00'"):
    """Simpler version using PostgreSQL EXTRACT on cast TIME."""
    return f"EXTRACT(EPOCH FROM COALESCE({col}, {fallback})::time)::int"


# ── Roster helpers ─────────────────────────────────────────────────────────────
def recompute_roster_snapshots(game_id):
    """Rebuild roster_snapshots for a game from scratch."""
    execute("DELETE FROM roster_snapshots WHERE game_id = :game_id", {"game_id": game_id})

    entries = query("""
        SELECT re.person_id, re.team_id, re.entered_at, re.exited_at, re.is_goalkeeper,
               p.name
        FROM roster_entries re
        JOIN people p ON p.id = re.person_id
        WHERE re.game_id = :game_id
        ORDER BY re.entered_at ASC
    """, {"game_id": game_id})

    if not entries:
        return

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

    for ts in timestamps:
        ts_secs = time_to_secs(ts)
        active = {}

        for e in entries:
            enter_secs = time_to_secs(e["entered_at"])
            exit_secs  = time_to_secs(e["exited_at"]) if e["exited_at"] else float('inf')

            if enter_secs <= ts_secs and exit_secs > ts_secs:
                pid = e["person_id"]
                if pid not in active or enter_secs > active[pid]["enter_secs"]:
                    active[pid] = {
                        "team_id":       e["team_id"],
                        "is_goalkeeper": e["is_goalkeeper"],
                        "enter_secs":    enter_secs,
                    }

        for pid, data in active.items():
            execute("""
                INSERT INTO roster_snapshots
                    (game_id, valid_from, team_id, person_id, is_goalkeeper)
                VALUES (:game_id, :valid_from, :team_id, :person_id, :is_goalkeeper)
            """, {
                "game_id":       game_id,
                "valid_from":    ts,
                "team_id":       data["team_id"],
                "person_id":     pid,
                "is_goalkeeper": data["is_goalkeeper"],
            })


def recompute_player_stats(person_id=None):
    """Rebuild player_stats_cache for one player or all players."""

    if person_id is not None:
        entries = query("""
            SELECT re.person_id, re.team_id, re.is_goalkeeper,
                   re.entered_at, re.exited_at,
                   COALESCE(g.video_end, '01:30:00') AS game_end,
                   g.video_start,
                   pr.arrived_at
            FROM roster_entries re
            JOIN games g ON g.id = re.game_id
            LEFT JOIN presences pr ON pr.person_id = re.person_id AND pr.game_id = re.game_id
            WHERE re.person_id = :person_id
        """, {"person_id": person_id})
        _upsert_stats(person_id, entries)
    else:
        all_entries = query("""
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

        for pid, group in groupby(all_entries, key=lambda r: r["person_id"]):
            _upsert_stats(pid, list(group))

        execute("""
            DELETE FROM player_stats_cache
            WHERE person_id NOT IN (SELECT DISTINCT person_id FROM roster_entries)
        """)


def _upsert_stats(person_id, entries):
    """Compute field/gk/late seconds from roster entry rows and upsert cache."""

    def hms_to_secs(t):
        if not t:
            return 0
        parts = t.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    field_seconds = 0
    gk_seconds    = 0
    seen_games_for_late = {}

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

        game_key = e["game_end"]
        if game_key not in seen_games_for_late:
            seen_games_for_late[game_key] = (e.get("arrived_at"), e.get("video_start"))

    late_seconds  = 0
    late_arrivals = 0
    for game_key, (arrived_at, video_start) in seen_games_for_late.items():
        if not arrived_at:
            continue
        kickoff = hms_to_secs(video_start) if video_start else 0
        late = max(0, hms_to_secs(arrived_at) - kickoff)
        if late > 0:
            late_seconds  += late
            late_arrivals += 1

    execute("""
        INSERT INTO player_stats_cache
            (person_id, field_seconds, gk_seconds, late_seconds, late_arrivals, updated_at)
        VALUES (:person_id, :field_seconds, :gk_seconds, :late_seconds, :late_arrivals, NOW())
        ON CONFLICT(person_id) DO UPDATE SET
            field_seconds  = EXCLUDED.field_seconds,
            gk_seconds     = EXCLUDED.gk_seconds,
            late_seconds   = EXCLUDED.late_seconds,
            late_arrivals  = EXCLUDED.late_arrivals,
            updated_at     = EXCLUDED.updated_at
    """, {
        "person_id":    person_id,
        "field_seconds": field_seconds,
        "gk_seconds":   gk_seconds,
        "late_seconds": late_seconds,
        "late_arrivals": late_arrivals,
    })


def get_steps_done(game_id):
    """Return a set of step numbers (1–6) that have data for this game."""
    if not game_id:
        return set()

    done = {1}
    checks = [
        (2, "SELECT 1 FROM teams          WHERE game_id = :gid LIMIT 1"),
        (3, "SELECT 1 FROM whatsapp_list  WHERE game_id = :gid LIMIT 1"),
        (4, "SELECT 1 FROM presences      WHERE game_id = :gid LIMIT 1"),
        (5, "SELECT 1 FROM roster_entries WHERE game_id = :gid LIMIT 1"),
        (6, "SELECT 1 FROM roster_entries WHERE game_id = :gid LIMIT 1"),
    ]
    for step, sql in checks:
        if query(sql, {"gid": game_id}):
            done.add(step)

    return done


def get_roster_timeline(game_id):
    """Return the full roster timeline as a structured dict for JSON serialization."""
    rows = query("""
        SELECT rs.valid_from, rs.person_id, rs.team_id, rs.is_goalkeeper,
               p.name, t.color AS team_color
        FROM roster_snapshots rs
        JOIN people p ON p.id = rs.person_id
        JOIN teams  t ON t.id = rs.team_id
        WHERE rs.game_id = :game_id
        ORDER BY rs.valid_from ASC, t.color, rs.is_goalkeeper DESC, p.name
    """, {"game_id": game_id})

    timeline = OrderedDict()
    for row in rows:
        ts = row["valid_from"]
        if ts not in timeline:
            timeline[ts] = []
        timeline[ts].append({
            "person_id":     row["person_id"],
            "name":          row["name"],
            "team_id":       row["team_id"],
            "team_color":    row["team_color"],
            "is_goalkeeper": row["is_goalkeeper"],
        })

    return {
        "timestamps": list(timeline.keys()),
        "snapshots":  dict(timeline),
    }


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


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    """Personal dashboard — show user's stats and recent events."""

    person_id = session.get("person_id")

    if not person_id:
        recent_games = query("""
            SELECT g.id, g.title, g.date, l.name AS location
            FROM games g
            JOIN locations l ON l.id = g.location_id
            ORDER BY g.date DESC
            LIMIT 5
        """)
        return render_template("index.html", linked=False, recent_games=recent_games)

    person = query("SELECT * FROM people WHERE id = :id", {"id": person_id})[0]

    stats = query("""
        SELECT
            COUNT(DISTINCT CASE WHEN e.type = 'goal' THEN e.id END) AS goals,
            COUNT(DISTINCT CASE WHEN el.link_type = 'assist' THEN el.id END) AS assists,
            COUNT(DISTINCT pr.game_id) AS appearances
        FROM people p
        LEFT JOIN events e       ON e.person_id = p.id
        LEFT JOIN event_links el ON el.linked_person_id = p.id
        LEFT JOIN presences pr   ON pr.person_id = p.id
        WHERE p.id = :person_id
    """, {"person_id": person_id})[0]

    recent_events = query("""
        SELECT
            e.*,
            g.title AS game_title,
            g.date  AS game_date,
            g.youtube_url
        FROM events e
        JOIN games g ON g.id = e.game_id
        WHERE e.person_id = :person_id
          AND e.type IN ('goal', 'highlight')
        ORDER BY g.date DESC, e.timestamp ASC
        LIMIT 10
    """, {"person_id": person_id})

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

    for event in recent_events:
        event["links"] = query("""
            SELECT el.link_type, pe.name
            FROM event_links el
            JOIN people pe ON pe.id = el.linked_person_id
            WHERE el.event_id = :event_id
        """, {"event_id": event["id"]})
        event["youtube_embed"] = build_embed_url(event.get("youtube_url"))

    recent_games = query("""
        SELECT g.id, g.title, g.date, l.name AS location
        FROM presences pr
        JOIN games g     ON g.id = pr.game_id
        JOIN locations l ON l.id = g.location_id
        WHERE pr.person_id = :person_id
        ORDER BY g.date DESC
        LIMIT 5
    """, {"person_id": person_id})

    recent_video_game = query("""
        SELECT g.id, g.title, g.date, g.youtube_url
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = :person_id AND g.youtube_url IS NOT NULL AND g.youtube_url != ''
        ORDER BY g.date DESC
        LIMIT 1
    """, {"person_id": person_id})
    recent_video_game = recent_video_game[0] if recent_video_game else None

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
    session.clear()

    if request.method == "POST":
        if not request.form.get("username"):
            return apology("must provide username", 403)
        elif not request.form.get("password"):
            return apology("must provide password", 403)

        username = request.form.get("username").lower()
        rows = query("SELECT * FROM users WHERE username = :username", {"username": username})

        if len(rows) != 1 or not check_password_hash(rows[0]["hash"], request.form.get("password")):
            return apology("invalid username and/or password", 403)

        session["user_id"]  = rows[0]["id"]
        session["role"]     = rows[0]["role"]
        session["person_id"] = rows[0]["person_id"]
        return redirect("/")
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""
    session.clear()
    return redirect("/")


@app.route("/userregister", methods=["GET", "POST"])
def userregister():
    """Register user"""
    if request.method == "GET":
        return render_template("userregister.html")

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
        execute("INSERT INTO users (username, hash) VALUES(:username, :hash)", {"username": username, "hash": hashpass})
        return redirect("/")
    except Exception:
        return apology("Username already exists")


@app.route("/games")
@login_required
def games():
    """List all games, most recent first."""
    print(session.get("role"))
    games = query("""
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
        GROUP BY g.id, g.title, g.date, g.youtube_url, l.name
        ORDER BY g.date DESC
    """)
    return render_template("games.html", games=games)


@app.route("/games/<int:game_id>")
@login_required
def game(game_id):
    """Show a single game."""

    game = query("""
        SELECT g.*, l.name AS location
        FROM games g
        JOIN locations l ON l.id = g.location_id
        WHERE g.id = :game_id
    """, {"game_id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    events = query("""
        SELECT e.*, pe.name AS person_name
        FROM events e
        LEFT JOIN people pe ON pe.id = e.person_id
        WHERE e.game_id = :game_id
        ORDER BY e.timestamp ASC
    """, {"game_id": game_id})

    all_links = query("""
        SELECT el.event_id, el.link_type, pe.name
        FROM event_links el
        JOIN people pe ON pe.id = el.linked_person_id
        WHERE el.event_id IN (
            SELECT id FROM events WHERE game_id = :game_id
        )
    """, {"game_id": game_id})
    links_by_event = {}
    for lnk in all_links:
        links_by_event.setdefault(lnk["event_id"], []).append(lnk)

    all_subs = query("""
        SELECT sd.*,
               po.name AS player_off_name,
               pn.name AS player_on_name,
               t.color  AS team_color
        FROM substitution_details sd
        JOIN people po ON po.id = sd.player_off_id
        JOIN people pn ON pn.id = sd.player_on_id
        JOIN teams  t  ON t.id  = sd.team_id
        WHERE sd.event_id IN (
            SELECT id FROM events WHERE game_id = :game_id AND type = 'substitution'
        )
    """, {"game_id": game_id})
    subs_by_event = {s["event_id"]: s for s in all_subs}

    all_tcd = query("""
        SELECT tcd.*,
               tl.color AS leaving_color,
               te.color AS entering_color,
               ts.color AS staying_color
        FROM team_change_details tcd
        JOIN teams tl ON tl.id = tcd.leaving_team_id
        JOIN teams te ON te.id = tcd.entering_team_id
        LEFT JOIN teams ts ON ts.id = tcd.staying_team_id
        WHERE tcd.event_id IN (
            SELECT id FROM events WHERE game_id = :game_id AND type = 'team_change'
        )
    """, {"game_id": game_id})
    tcd_by_event = {t["event_id"]: t for t in all_tcd}

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

    segments = query("""
        SELECT s.*, ta.color AS team_a_color, tb.color AS team_b_color,
               ta.id AS team_a_id, tb.id AS team_b_id
        FROM segments s
        JOIN teams ta ON ta.id = s.team_a_id
        JOIN teams tb ON tb.id = s.team_b_id
        WHERE s.game_id = :game_id
        ORDER BY s.started_at ASC
    """, {"game_id": game_id})

    all_goals = query("""
        SELECT e.timestamp, re.team_id
        FROM events e
        JOIN roster_entries re
          ON re.person_id  = e.person_id
         AND re.game_id    = e.game_id
         AND re.entered_at <= e.timestamp
         AND (re.exited_at IS NULL OR re.exited_at > e.timestamp)
        WHERE e.game_id = :game_id
          AND e.type    = 'goal'
        ORDER BY e.timestamp
    """, {"game_id": game_id})

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

    roster_timeline = get_roster_timeline(game_id)
    players = roster_timeline["snapshots"].get("00:00:00", [])

    seek_seconds = None
    t_param = request.args.get("t", "").strip()
    if t_param:
        parts = t_param.split(":")
        if len(parts) == 3:
            try:
                seek_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                seek_seconds = None

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
    game_start_event = query(
        "SELECT timestamp FROM events WHERE game_id = :game_id AND type = 'game_start' LIMIT 1",
        {"game_id": game_id}
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
    locations = query("SELECT id, name FROM locations ORDER BY name")

    if request.method == "GET":
        return render_template("register_game.html", locations=locations)

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

    game_id = execute("""
        INSERT INTO games (title, date, location_id, youtube_url, video_start, video_end)
        VALUES (:title, :date, :location_id, :youtube, :vid_start, :vid_end)
        RETURNING id
    """, {
        "title":       title,
        "date":        date,
        "location_id": location_id,
        "youtube":     youtube or None,
        "vid_start":   vid_start or None,
        "vid_end":     vid_end or None,
    })

    return redirect(f"/games/{game_id}/teams")


@app.route("/games/<int:game_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def game_edit(game_id):
    game = query("SELECT * FROM games WHERE id = :id", {"id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    locations = query("SELECT * FROM locations ORDER BY name")

    if request.method == "GET":
        steps_done = get_steps_done(game_id)
        return render_template("game_edit.html", game=game, locations=locations, steps_done=steps_done)

    title       = request.form.get("title", "").strip()
    date        = request.form.get("date", "").strip()
    location_id = request.form.get("location_id", "").strip()
    youtube     = request.form.get("youtube_url", "").strip() or None
    vid_start   = request.form.get("video_start", "").strip() or None
    vid_end     = request.form.get("video_end", "").strip() or None

    if not title or not date or not location_id:
        return apology("Title, date and location are required")

    execute("""
        UPDATE games SET title = :title, date = :date, location_id = :location_id,
                         youtube_url = :youtube, video_start = :vid_start, video_end = :vid_end
        WHERE id = :game_id
    """, {
        "title": title, "date": date, "location_id": location_id,
        "youtube": youtube, "vid_start": vid_start, "vid_end": vid_end, "game_id": game_id,
    })

    flash("Game updated!")
    return redirect(f"/games/{game_id}")


@app.route("/games/<int:game_id>/teams", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def manage_teams(game_id):
    """Define teams for a game."""

    game = query("SELECT * FROM games WHERE id = :id", {"id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    teams = query("SELECT * FROM teams WHERE game_id = :game_id ORDER BY id", {"game_id": game_id})

    initial_segment = query("""
        SELECT * FROM segments WHERE game_id = :game_id AND started_at = '00:00:00'
    """, {"game_id": game_id})
    initial_segment = initial_segment[0] if initial_segment else None

    if request.method == "GET":
        steps_done = get_steps_done(game_id)
        return render_template("teams.html",
            game=game, teams=teams, steps_done=steps_done, initial_segment=initial_segment)

    action = request.form.get("action")

    if action == "add":
        color  = request.form.get("color", "").strip().lower()
        custom = request.form.get("custom_color", "").strip().lower()
        if color == "custom":
            color = custom
        if not color:
            flash("Please enter a team color.")
            return redirect(f"/games/{game_id}/teams")
        try:
            execute("INSERT INTO teams (game_id, color) VALUES (:game_id, :color)",
                    {"game_id": game_id, "color": color})
        except Exception:
            flash(f"'{color}' already exists for this game.")
        return redirect(f"/games/{game_id}/teams")

    if action == "delete":
        team_id = request.form.get("team_id")
        execute("DELETE FROM teams WHERE id = :team_id AND game_id = :game_id",
                {"team_id": team_id, "game_id": game_id})
        execute("""
            DELETE FROM segments
            WHERE game_id = :game_id AND started_at = '00:00:00'
              AND (team_a_id = :team_id OR team_b_id = :team_id)
        """, {"game_id": game_id, "team_id": team_id})
        return redirect(f"/games/{game_id}/teams")

    if action == "set_start":
        team_a_id = request.form.get("start_team_a", "").strip()
        team_b_id = request.form.get("start_team_b", "").strip()

        if not team_a_id or not team_b_id:
            flash("Please select both starting teams.")
            return redirect(f"/games/{game_id}/teams")
        if team_a_id == team_b_id:
            flash("Starting teams must be different.")
            return redirect(f"/games/{game_id}/teams")

        execute("DELETE FROM segments WHERE game_id = :game_id AND started_at = '00:00:00'",
                {"game_id": game_id})
        execute("""
            INSERT INTO segments (game_id, team_a_id, team_b_id, started_at)
            VALUES (:game_id, :team_a_id, :team_b_id, '00:00:00')
        """, {"game_id": game_id, "team_a_id": team_a_id, "team_b_id": team_b_id})
        return redirect(f"/games/{game_id}/teams")

    if action == "next":
        if len(teams) < 2:
            flash("You need at least 2 teams before continuing.")
            return redirect(f"/games/{game_id}/teams")
        if not initial_segment:
            seg = query("SELECT id FROM segments WHERE game_id = :game_id AND started_at = '00:00:00'",
                        {"game_id": game_id})
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

    game = query("SELECT * FROM games WHERE id = :id", {"id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    if request.method == "GET":
        people = query("""
            SELECT p.id, p.name, p.nickname,
                   CASE WHEN w.person_id IS NOT NULL THEN 1 ELSE 0 END AS on_list
            FROM people p
            LEFT JOIN whatsapp_list w ON w.person_id = p.id AND w.game_id = :game_id
            ORDER BY p.name
        """, {"game_id": game_id})
        steps_done = get_steps_done(game_id)
        return render_template("attendance.html", game=game, people=people, steps_done=steps_done)

    selected = request.form.getlist("person_id")
    execute("DELETE FROM whatsapp_list WHERE game_id = :game_id", {"game_id": game_id})
    for pid in selected:
        execute("""
            INSERT INTO whatsapp_list (game_id, person_id, timestamp)
            VALUES (:game_id, :person_id, NOW())
        """, {"game_id": game_id, "person_id": pid})

    return redirect(f"/games/{game_id}/presences")


@app.route("/games/<int:game_id>/presences", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def presences(game_id):
    """Manage who attended the game."""

    game = query("SELECT * FROM games WHERE id = :id", {"id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    if request.method == "GET":
        people = query("""
            SELECT p.id, p.name, p.nickname,
                   CASE WHEN pr.person_id IS NOT NULL THEN 1 ELSE 0 END AS present,
                   pr.arrived_at
            FROM people p
            LEFT JOIN whatsapp_list w ON w.person_id = p.id AND w.game_id = :game_id
            LEFT JOIN presences pr    ON pr.person_id = p.id AND pr.game_id = :game_id
            WHERE w.person_id IS NOT NULL
            ORDER BY p.name
        """, {"game_id": game_id})

        others = query("""
            SELECT p.id, p.name, p.nickname,
                   CASE WHEN pr.person_id IS NOT NULL THEN 1 ELSE 0 END AS present,
                   pr.arrived_at
            FROM people p
            LEFT JOIN whatsapp_list w ON w.person_id = p.id AND w.game_id = :game_id
            LEFT JOIN presences pr    ON pr.person_id = p.id AND pr.game_id = :game_id
            WHERE w.person_id IS NULL
            ORDER BY p.name
        """, {"game_id": game_id})

        steps_done = get_steps_done(game_id)
        return render_template("presences.html",
            steps_done=steps_done, game=game, people=people, others=others)

    selected = request.form.getlist("person_id")
    execute("DELETE FROM presences WHERE game_id = :game_id", {"game_id": game_id})
    for pid in selected:
        arrived_at = request.form.get(f"arrived_at_{pid}", "").strip() or None
        if arrived_at:
            if not re.match(r'^\d{2}:\d{2}:\d{2}$', arrived_at):
                arrived_at = None
        execute("""
            INSERT INTO presences (game_id, person_id, arrived_at)
            VALUES (:game_id, :person_id, :arrived_at)
        """, {"game_id": game_id, "person_id": pid, "arrived_at": arrived_at})

    return redirect(f"/games/{game_id}/roster")


@app.route("/games/<int:game_id>/roster", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def manage_roster(game_id):
    """Assign players to teams with goalkeeper designation."""

    game = query("SELECT * FROM games WHERE id = :id", {"id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    teams = query("SELECT * FROM teams WHERE game_id = :game_id ORDER BY id", {"game_id": game_id})
    if not teams:
        flash("Define teams before setting the roster.")
        return redirect(f"/games/{game_id}/teams")

    players = query("""
        SELECT p.id, p.name, p.nickname,
               re.team_id, re.is_goalkeeper
        FROM presences pr
        JOIN people p ON p.id = pr.person_id
        LEFT JOIN roster_entries re
               ON re.person_id = p.id
              AND re.game_id   = :game_id
              AND re.entered_at = '00:00:00'
        WHERE pr.game_id = :game_id
        ORDER BY p.name
    """, {"game_id": game_id})

    if request.method == "GET":
        steps_done = get_steps_done(game_id)
        return render_template("roster.html", game=game, teams=teams, players=players, steps_done=steps_done)

    errors = []
    assignments = {}

    for player in players:
        pid          = str(player["id"])
        team_id      = request.form.get(f"team_{pid}", "").strip()
        is_goalkeeper = 1 if request.form.get(f"gk_{pid}") else 0
        if team_id:
            assignments[pid] = {"team_id": team_id, "is_goalkeeper": is_goalkeeper}

    gk_per_team      = defaultdict(int)
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
        steps_done = get_steps_done(game_id)
        return render_template("roster.html", game=game, teams=teams, players=players, steps_done=steps_done)

    execute("""
        DELETE FROM roster_entries
        WHERE game_id = :game_id AND entered_at = '00:00:00'
    """, {"game_id": game_id})

    for pid, data in assignments.items():
        execute("""
            INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
            VALUES (:game_id, :person_id, :team_id, '00:00:00', :is_goalkeeper)
        """, {
            "game_id":      game_id,
            "person_id":    pid,
            "team_id":      data["team_id"],
            "is_goalkeeper": data["is_goalkeeper"],
        })

    recompute_player_stats()
    recompute_roster_snapshots(game_id)
    return redirect(f"/games/{game_id}/events")


@app.route("/games/<int:game_id>/events", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def log_events(game_id):
    """Live event logging for a game."""

    game = query("""
        SELECT g.*, l.name AS location
        FROM games g
        JOIN locations l ON l.id = g.location_id
        WHERE g.id = :game_id
    """, {"game_id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    teams   = query("SELECT * FROM teams WHERE game_id = :game_id ORDER BY id", {"game_id": game_id})
    players = query("""
        SELECT p.id, p.name, p.nickname, t.color AS team_color, t.id AS team_id
        FROM roster_entries re
        JOIN people p ON p.id = re.person_id
        LEFT JOIN teams t ON t.id = re.team_id
        WHERE re.game_id = :game_id AND re.entered_at = '00:00:00'
        ORDER BY t.color, p.name
    """, {"game_id": game_id})

    if request.method == "GET":
        events = query("""
            SELECT e.*, pe.name AS person_name
            FROM events e
            LEFT JOIN people pe ON pe.id = e.person_id
            WHERE e.game_id = :game_id
            ORDER BY e.timestamp ASC
        """, {"game_id": game_id})

        for event in events:
            if event["type"] == "substitution":
                sd = query("""
                    SELECT sd.*,
                           po.name AS player_off_name,
                           pn.name AS player_on_name,
                           t.color AS team_color
                    FROM substitution_details sd
                    JOIN people po ON po.id = sd.player_off_id
                    JOIN people pn ON pn.id = sd.player_on_id
                    JOIN teams  t  ON t.id  = sd.team_id
                    WHERE sd.event_id = :event_id
                """, {"event_id": event["id"]})
                event["substitution"] = sd[0] if sd else None
                event["team_change"]  = None
            elif event["type"] == "team_change":
                tcd = query("""
                    SELECT tcd.*, tl.color AS leaving_color,
                           te.color AS entering_color,
                           ts.color AS staying_color
                    FROM team_change_details tcd
                    JOIN teams tl ON tl.id = tcd.leaving_team_id
                    JOIN teams te ON te.id = tcd.entering_team_id
                    LEFT JOIN teams ts ON ts.id = tcd.staying_team_id
                    WHERE tcd.event_id = :event_id
                """, {"event_id": event["id"]})
                event["team_change"]  = tcd[0] if tcd else None
                event["substitution"] = None
            elif event["type"] == "player_join":
                jr = query("""
                    SELECT t.color AS team_color, re.is_goalkeeper
                    FROM roster_entries re
                    JOIN teams t ON t.id = re.team_id
                    WHERE re.game_id = :game_id AND re.person_id = :person_id AND re.entered_at = :ts
                    LIMIT 1
                """, {"game_id": game_id, "person_id": event["person_id"], "ts": event["timestamp"]})
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
                event["links"] = query("""
                    SELECT el.link_type, pe.name
                    FROM event_links el
                    JOIN people pe ON pe.id = el.linked_person_id
                    WHERE el.event_id = :event_id
                """, {"event_id": event["id"]})
                event["team_change"]  = None
                event["substitution"] = None
                event["player_join"]  = None

        active_segment = query("""
            SELECT s.*, ta.color AS team_a_color, tb.color AS team_b_color
            FROM segments s
            JOIN teams ta ON ta.id = s.team_a_id
            JOIN teams tb ON tb.id = s.team_b_id
            WHERE s.game_id = :game_id AND s.ended_at IS NULL
            ORDER BY s.started_at DESC LIMIT 1
        """, {"game_id": game_id})
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

        all_players = query("""
            SELECT p.id, p.name, p.nickname
            FROM presences pr
            JOIN people p ON p.id = pr.person_id
            WHERE pr.game_id = :game_id
            ORDER BY p.name
        """, {"game_id": game_id})

        import json
        roster_timeline = get_roster_timeline(game_id)

        segments = query("""
            SELECT s.id, s.team_a_id, s.team_b_id, s.started_at, s.ended_at,
                   ta.color AS team_a_color, tb.color AS team_b_color
            FROM segments s
            JOIN teams ta ON ta.id = s.team_a_id
            JOIN teams tb ON tb.id = s.team_b_id
            WHERE s.game_id = :game_id
            ORDER BY s.started_at
        """, {"game_id": game_id})

        all_goals = query("""
            SELECT e.timestamp, re.team_id
            FROM events e
            JOIN roster_entries re
              ON re.person_id  = e.person_id
             AND re.game_id    = e.game_id
             AND re.entered_at <= e.timestamp
             AND (re.exited_at IS NULL OR re.exited_at > e.timestamp)
            WHERE e.game_id = :game_id
              AND e.type    = 'goal'
            ORDER BY e.timestamp
        """, {"game_id": game_id})

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

        game_start_event = query(
            "SELECT timestamp FROM events WHERE game_id = :game_id AND type = 'game_start' LIMIT 1",
            {"game_id": game_id}
        )
        game_start_event = game_start_event[0] if game_start_event else None

        steps_done = get_steps_done(game_id)
        return render_template("events.html",
            game=game, players=players, all_players=all_players, teams=teams,
            events=events, active_segment=active_segment, youtube_embed=youtube_embed,
            roster_timeline_json=json.dumps(roster_timeline), segments=segments,
            steps_done=steps_done, game_start_event=game_start_event,
        )

    # ── POST ──────────────────────────────────────────────────────────────────
    event_type = request.form.get("type", "").strip()
    person_id  = request.form.get("person_id", "").strip() or None
    timestamp  = request.form.get("timestamp", "").strip()
    notes      = request.form.get("notes", "").strip() or None
    duration   = request.form.get("duration", "20").strip() or "20"

    if not event_type or not timestamp:
        return jsonify({"success": False, "error": "Type and timestamp are required"}), 400

    if event_type == "player_join":
        person_id = request.form.get("join_person_id", "").strip() or person_id

    event_id = execute("""
        INSERT INTO events (game_id, person_id, type, timestamp, duration, notes)
        VALUES (:game_id, :person_id, :type, :timestamp, :duration, :notes)
        RETURNING id
    """, {
        "game_id":   game_id,
        "person_id": person_id,
        "type":      event_type,
        "timestamp": timestamp,
        "duration":  int(duration),
        "notes":     notes,
    })

    response_data = {
        "success": True,
        "event": {
            "id":           event_id,
            "type":         event_type,
            "timestamp":    timestamp,
            "notes":        notes,
            "person_id":    int(person_id) if person_id else None,
            "person_name":  None,
            "substitution": None,
            "team_change":  None,
            "links":        [],
        }
    }

    if person_id:
        row = query("SELECT name FROM people WHERE id = :id", {"id": person_id})
        response_data["event"]["person_name"] = row[0]["name"] if row else None

    # ── Substitution ──────────────────────────────────────────────────────────
    if event_type == "substitution":
        player_off_id = request.form.get("player_off_id", "").strip()
        player_on_id  = request.form.get("player_on_id", "").strip()

        if player_off_id and player_on_id:
            is_gk_swap = 1 if request.form.get("is_goalkeeper_swap") else 0

            off_entry = query("""
                SELECT team_id, is_goalkeeper FROM roster_entries
                WHERE game_id = :game_id AND person_id = :person_id AND exited_at IS NULL
                ORDER BY entered_at DESC LIMIT 1
            """, {"game_id": game_id, "person_id": player_off_id})

            on_entry = query("""
                SELECT team_id, is_goalkeeper FROM roster_entries
                WHERE game_id = :game_id AND person_id = :person_id AND exited_at IS NULL
                ORDER BY entered_at DESC LIMIT 1
            """, {"game_id": game_id, "person_id": player_on_id})

            off_team_id = off_entry[0]["team_id"]      if off_entry else None
            off_is_gk   = off_entry[0]["is_goalkeeper"] if off_entry else 0
            on_team_id  = on_entry[0]["team_id"]       if on_entry  else None
            on_is_gk    = on_entry[0]["is_goalkeeper"]  if on_entry  else 0

            if is_gk_swap:
                new_off_is_gk = 0
                new_on_is_gk  = 1
                new_off_team  = off_team_id
                new_on_team   = off_team_id
            else:
                new_off_is_gk = off_is_gk
                new_on_is_gk  = on_is_gk
                new_off_team  = on_team_id
                new_on_team   = off_team_id

            team_id = off_team_id

            execute("""
                INSERT INTO substitution_details
                    (event_id, player_off_id, player_on_id, team_id, is_goalkeeper_swap)
                VALUES (:event_id, :player_off_id, :player_on_id, :team_id, :is_gk_swap)
            """, {
                "event_id": event_id, "player_off_id": player_off_id,
                "player_on_id": player_on_id, "team_id": team_id or 0, "is_gk_swap": is_gk_swap,
            })

            execute("""
                UPDATE roster_entries SET exited_at = :timestamp
                WHERE game_id = :game_id AND person_id = :person_id AND exited_at IS NULL
            """, {"timestamp": timestamp, "game_id": game_id, "person_id": player_off_id})

            if not is_gk_swap:
                execute("""
                    UPDATE roster_entries SET exited_at = :timestamp
                    WHERE game_id = :game_id AND person_id = :person_id AND exited_at IS NULL
                """, {"timestamp": timestamp, "game_id": game_id, "person_id": player_on_id})

            execute("""
                INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
                VALUES (:game_id, :person_id, :team_id, :timestamp, :is_goalkeeper)
            """, {
                "game_id": game_id, "person_id": player_on_id,
                "team_id": new_on_team, "timestamp": timestamp, "is_goalkeeper": new_on_is_gk,
            })

            if new_off_team:
                execute("""
                    INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
                    VALUES (:game_id, :person_id, :team_id, :timestamp, :is_goalkeeper)
                """, {
                    "game_id": game_id, "person_id": player_off_id,
                    "team_id": new_off_team, "timestamp": timestamp, "is_goalkeeper": new_off_is_gk,
                })

            off  = query("SELECT name FROM people WHERE id = :id", {"id": player_off_id})
            on   = query("SELECT name FROM people WHERE id = :id", {"id": player_on_id})
            team = query("SELECT color FROM teams WHERE id = :id", {"id": team_id}) if team_id else []
            response_data["event"]["substitution"] = {
                "player_off_name":    off[0]["name"]   if off  else "",
                "player_on_name":     on[0]["name"]    if on   else "",
                "team_color":         team[0]["color"] if team else "",
                "is_goalkeeper_swap": is_gk_swap,
            }
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    # ── Game Start ────────────────────────────────────────────────────────────
    if event_type == "game_start":
        first_segment = None
        if len(teams) >= 2:
            execute("DELETE FROM segments WHERE game_id = :game_id AND started_at < :timestamp",
                    {"game_id": game_id, "timestamp": timestamp})
            existing = query(
                "SELECT id FROM segments WHERE game_id = :game_id AND started_at = :timestamp",
                {"game_id": game_id, "timestamp": timestamp}
            )
            if not existing:
                execute("""
                    INSERT INTO segments (game_id, team_a_id, team_b_id, started_at)
                    VALUES (:game_id, :team_a_id, :team_b_id, :timestamp)
                """, {
                    "game_id":   game_id,
                    "team_a_id": teams[0]["id"],
                    "team_b_id": teams[1]["id"],
                    "timestamp": timestamp,
                })
            first_segment = {
                "team_a_color": teams[0]["color"],
                "team_b_color": teams[1]["color"],
            }
        response_data["event"]["game_start"] = {"timestamp": timestamp}
        response_data["game_started"]  = True
        response_data["first_segment"] = first_segment

    elif event_type == "team_change":
        leaving_team_id  = request.form.get("leaving_team_id", "").strip()
        entering_team_id = request.form.get("entering_team_id", "").strip()

        if leaving_team_id and entering_team_id:
            active_seg = query("""
                SELECT * FROM segments WHERE game_id = :game_id AND ended_at IS NULL
                ORDER BY started_at DESC LIMIT 1
            """, {"game_id": game_id})

            staying_team_id = None
            if active_seg:
                seg = active_seg[0]
                staying_team_id = (
                    seg["team_b_id"]
                    if str(seg["team_a_id"]) == leaving_team_id
                    else seg["team_a_id"]
                )

            execute("""
                INSERT INTO team_change_details
                    (event_id, leaving_team_id, entering_team_id, staying_team_id)
                VALUES (:event_id, :leaving_team_id, :entering_team_id, :staying_team_id)
            """, {
                "event_id": event_id, "leaving_team_id": leaving_team_id,
                "entering_team_id": entering_team_id, "staying_team_id": staying_team_id,
            })

            execute("""
                UPDATE segments SET ended_at = :timestamp
                WHERE game_id = :game_id AND ended_at IS NULL
            """, {"timestamp": timestamp, "game_id": game_id})

            new_a = staying_team_id or leaving_team_id
            new_b = entering_team_id
            execute("""
                INSERT INTO segments (game_id, team_a_id, team_b_id, started_at)
                VALUES (:game_id, :team_a_id, :team_b_id, :timestamp)
            """, {"game_id": game_id, "team_a_id": new_a, "team_b_id": new_b, "timestamp": timestamp})

            staying  = query("SELECT color FROM teams WHERE id = :id", {"id": new_a})
            entering = query("SELECT color FROM teams WHERE id = :id", {"id": entering_team_id})
            response_data["event"]["team_change"] = {
                "staying_color":  staying[0]["color"]  if staying  else "",
                "entering_color": entering[0]["color"] if entering else "",
            }
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    elif event_type == "player_join":
        join_team_id        = request.form.get("join_team_id", "").strip() or None
        join_person_id      = request.form.get("join_person_id", "").strip() or None
        is_goalkeeper       = 1 if request.form.get("join_is_goalkeeper") else 0
        effective_person_id = join_person_id or person_id

        if effective_person_id and join_team_id:
            prow = query("SELECT name FROM people WHERE id = :id", {"id": effective_person_id})
            if prow:
                response_data["event"]["person_id"]   = int(effective_person_id)
                response_data["event"]["person_name"] = prow[0]["name"]

            execute("""
                UPDATE roster_entries SET exited_at = :timestamp
                WHERE game_id = :game_id AND person_id = :person_id AND exited_at IS NULL
            """, {"timestamp": timestamp, "game_id": game_id, "person_id": effective_person_id})

            execute("""
                INSERT INTO roster_entries (game_id, person_id, team_id, entered_at, is_goalkeeper)
                VALUES (:game_id, :person_id, :team_id, :timestamp, :is_goalkeeper)
            """, {
                "game_id": game_id, "person_id": effective_person_id,
                "team_id": join_team_id, "timestamp": timestamp, "is_goalkeeper": is_goalkeeper,
            })

            team_row = query("SELECT color FROM teams WHERE id = :id", {"id": join_team_id})
            response_data["event"]["player_join"] = {
                "team_color":   team_row[0]["color"] if team_row else "",
                "is_goalkeeper": is_goalkeeper,
            }
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    elif event_type == "player_leave":
        if person_id:
            execute("""
                UPDATE roster_entries SET exited_at = :timestamp
                WHERE game_id = :game_id AND person_id = :person_id AND exited_at IS NULL
            """, {"timestamp": timestamp, "game_id": game_id, "person_id": person_id})
            response_data["event"]["player_leave"] = True
            recompute_roster_snapshots(game_id)
            response_data["roster_timeline"] = get_roster_timeline(game_id)

    else:
        linked_person_id = request.form.get("linked_person_id", "").strip() or None
        link_type        = request.form.get("link_type", "").strip() or None

        if linked_person_id and link_type:
            execute("""
                INSERT INTO event_links (event_id, linked_person_id, link_type)
                VALUES (:event_id, :linked_person_id, :link_type)
            """, {"event_id": event_id, "linked_person_id": linked_person_id, "link_type": link_type})

            linked = query("SELECT name FROM people WHERE id = :id", {"id": linked_person_id})
            response_data["event"]["links"] = [{
                "link_type": link_type,
                "name": linked[0]["name"] if linked else "",
            }]

    return jsonify(response_data)


@app.route("/games/<int:game_id>/events/<int:event_id>", methods=["GET"])
@login_required
@role_required("admin", "editor")
def get_event(game_id, event_id):
    """Get a single event's data for editing."""
    event = query("""
        SELECT e.*, pe.name AS person_name
        FROM events e
        LEFT JOIN people pe ON pe.id = e.person_id
        WHERE e.id = :event_id AND e.game_id = :game_id
    """, {"event_id": event_id, "game_id": game_id})

    if not event:
        return jsonify({"success": False, "error": "Event not found"}), 404

    event = event[0]

    link = query("""
        SELECT link_type, linked_person_id
        FROM event_links
        WHERE event_id = :event_id
    """, {"event_id": event_id})
    event["link"] = link[0] if link else None

    if event["type"] == "team_change":
        team_change = query("""
            SELECT leaving_team_id, entering_team_id, staying_team_id
            FROM team_change_details
            WHERE event_id = :event_id
        """, {"event_id": event_id})
        event["team_change_detail"] = team_change[0] if team_change else None

    return jsonify(event)


@app.route("/games/<int:game_id>/events/<int:event_id>", methods=["PUT"])
@login_required
@role_required("admin", "editor")
def update_event(game_id, event_id):
    """Update an existing event."""
    existing = query("SELECT * FROM events WHERE id = :id AND game_id = :game_id",
                     {"id": event_id, "game_id": game_id})
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

    execute("""
        UPDATE events
        SET type = :type, person_id = :person_id, timestamp = :timestamp,
            duration = :duration, notes = :notes
        WHERE id = :event_id
    """, {
        "type": event_type, "person_id": person_id, "timestamp": timestamp,
        "duration": int(duration), "notes": notes, "event_id": event_id,
    })

    execute("DELETE FROM event_links WHERE event_id = :event_id", {"event_id": event_id})
    if linked_person_id and link_type:
        execute("""
            INSERT INTO event_links (event_id, linked_person_id, link_type)
            VALUES (:event_id, :linked_person_id, :link_type)
        """, {"event_id": event_id, "linked_person_id": linked_person_id, "link_type": link_type})

    team_change_data = None
    if event_type == "team_change":
        leaving_team_id  = request.form.get("leaving_team_id", "").strip()
        entering_team_id = request.form.get("entering_team_id", "").strip()

        if leaving_team_id and entering_team_id:
            execute("DELETE FROM team_change_details WHERE event_id = :event_id", {"event_id": event_id})

            active_seg = query("""
                SELECT * FROM segments WHERE game_id = :game_id AND ended_at IS NULL
                ORDER BY started_at DESC LIMIT 1
            """, {"game_id": game_id})

            staying_team_id = None
            if active_seg:
                seg = active_seg[0]
                staying_team_id = seg["team_b_id"] if str(seg["team_a_id"]) == leaving_team_id else seg["team_a_id"]

            execute("""
                INSERT INTO team_change_details (event_id, leaving_team_id, entering_team_id, staying_team_id)
                VALUES (:event_id, :leaving_team_id, :entering_team_id, :staying_team_id)
            """, {
                "event_id": event_id, "leaving_team_id": leaving_team_id,
                "entering_team_id": entering_team_id, "staying_team_id": staying_team_id,
            })

            staying_team = query("SELECT color FROM teams WHERE id = :id",
                                 {"id": staying_team_id or leaving_team_id})
            entering     = query("SELECT color FROM teams WHERE id = :id", {"id": entering_team_id})
            team_change_data = {
                "staying_color":  staying_team[0]["color"] if staying_team else "",
                "entering_color": entering[0]["color"]     if entering     else "",
            }

    person_name = None
    if person_id:
        row = query("SELECT name FROM people WHERE id = :id", {"id": person_id})
        person_name = row[0]["name"] if row else None

    linked_name = None
    if linked_person_id:
        row = query("SELECT name FROM people WHERE id = :id", {"id": linked_person_id})
        linked_name = row[0]["name"] if row else None

    return jsonify({
        "success": True,
        "event": {
            "id":          event_id,
            "type":        event_type,
            "timestamp":   timestamp,
            "person_name": person_name,
            "notes":       notes,
            "link_type":   link_type,
            "linked_name": linked_name,
            "team_change": team_change_data,
        }
    })


@app.route("/games/<int:game_id>/events/<int:event_id>", methods=["DELETE"])
@login_required
@role_required("admin", "editor")
def delete_event(game_id, event_id):
    """Delete an event."""
    existing = query("SELECT * FROM events WHERE id = :id AND game_id = :game_id",
                     {"id": event_id, "game_id": game_id})
    if not existing:
        return jsonify({"success": False, "error": "Event not found"}), 404

    try:
        event_type = existing[0]["type"]

        if event_type == "substitution":
            sd = query("SELECT * FROM substitution_details WHERE event_id = :event_id", {"event_id": event_id})
            if sd:
                sd = sd[0]
                ts = existing[0]["timestamp"]
                execute("""
                    DELETE FROM roster_entries
                    WHERE game_id = :game_id AND entered_at = :ts
                      AND person_id IN (:player_off_id, :player_on_id)
                """, {
                    "game_id": game_id, "ts": ts,
                    "player_off_id": sd["player_off_id"], "player_on_id": sd["player_on_id"],
                })
                execute("""
                    UPDATE roster_entries SET exited_at = NULL
                    WHERE game_id = :game_id AND exited_at = :ts
                      AND person_id IN (:player_off_id, :player_on_id)
                """, {
                    "game_id": game_id, "ts": ts,
                    "player_off_id": sd["player_off_id"], "player_on_id": sd["player_on_id"],
                })

        elif event_type == "player_join":
            ts  = existing[0]["timestamp"]
            pid = existing[0]["person_id"]
            execute("""
                DELETE FROM roster_entries
                WHERE game_id = :game_id AND person_id = :person_id AND entered_at = :ts
            """, {"game_id": game_id, "person_id": pid, "ts": ts})

        elif event_type == "player_leave":
            ts  = existing[0]["timestamp"]
            pid = existing[0]["person_id"]
            execute("""
                UPDATE roster_entries SET exited_at = NULL
                WHERE game_id = :game_id AND person_id = :person_id AND exited_at = :ts
            """, {"game_id": game_id, "person_id": pid, "ts": ts})

        elif event_type == "game_start":
            ts = existing[0]["timestamp"]
            execute("DELETE FROM segments WHERE game_id = :game_id AND started_at = :ts",
                    {"game_id": game_id, "ts": ts})

        execute("DELETE FROM event_links WHERE event_id = :event_id", {"event_id": event_id})
        execute("DELETE FROM team_change_details WHERE event_id = :event_id", {"event_id": event_id})
        execute("DELETE FROM substitution_details WHERE event_id = :event_id", {"event_id": event_id})
        execute("DELETE FROM events WHERE id = :event_id", {"event_id": event_id})

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

    filter_type = request.args.get("filter", "all")
    game_id     = request.args.get("game_id", "")
    timeframe   = request.args.get("timeframe", "")

    all_games = query("SELECT id, title, date FROM games ORDER BY date DESC")

    # Build WHERE clause — PostgreSQL uses EXTRACT for date parts
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
                f"EXTRACT(YEAR FROM g.date) = {int(year)} AND "
                f"EXTRACT(MONTH FROM g.date) BETWEEN {month_start} AND {month_end}"
            )
        else:
            where_clauses.append(f"EXTRACT(YEAR FROM g.date) = {int(timeframe)}")

    game_where = ("AND " + " AND ".join(where_clauses)) if where_clauses else ""

    if game_where:
        game_end_expr = _secs('NULL', fallback_col='g.video_end')
        time_subquery = f"""
            LEFT JOIN (
                SELECT
                    person_id,
                    LEAST(SUM_field, game_end) AS field_seconds,
                    LEAST(SUM_gk,    game_end) AS gk_seconds
                FROM (
                    SELECT
                        re.person_id,
                        {game_end_expr} AS game_end,
                        SUM(CASE WHEN re.team_id IS NOT NULL THEN
                            GREATEST(0, LEAST(
                                {_secs('re.exited_at')}  - {_secs_entered('re.entered_at')},
                                {game_end_expr}          - {_secs_entered('re.entered_at')}
                            ))
                        END) AS SUM_field,
                        SUM(CASE WHEN re.is_goalkeeper = 1 THEN
                            GREATEST(0, LEAST(
                                {_secs('re.exited_at')}  - {_secs_entered('re.entered_at')},
                                {game_end_expr}          - {_secs_entered('re.entered_at')}
                            ))
                        END) AS SUM_gk
                    FROM roster_entries re
                    JOIN games g ON g.id = re.game_id
                    WHERE 1=1 {game_where}
                    GROUP BY re.person_id, g.id, game_end
                ) sub
                GROUP BY person_id
            ) t ON t.person_id = p.id
        """
        late_subquery = f"""
            LEFT JOIN (
                SELECT
                    pr.person_id,
                    SUM(CASE
                        WHEN pr.arrived_at IS NOT NULL AND (
                            {_secs_entered('pr.arrived_at')}
                            >
                            {_secs_entered("COALESCE(g.video_start,'00:00:00')")}
                        ) THEN
                            {_secs_entered('pr.arrived_at')}
                            - {_secs_entered("COALESCE(g.video_start,'00:00:00')")}
                        ELSE 0
                    END) AS late_seconds,
                    SUM(CASE
                        WHEN pr.arrived_at IS NOT NULL AND (
                            {_secs_entered('pr.arrived_at')}
                            >
                            {_secs_entered("COALESCE(g.video_start,'00:00:00')")}
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
        time_subquery = "LEFT JOIN player_stats_cache t ON t.person_id = p.id"
        late_subquery = "LEFT JOIN player_stats_cache lt ON lt.person_id = p.id"

    bench_subquery = f"""
        LEFT JOIN (
            SELECT pr.person_id,
                SUM(
                    {_secs('NULL', fallback_col='g.video_end')}
                    -
                    {_secs_entered("COALESCE(pr.arrived_at,'00:00:00')")}
                ) AS total_game_seconds
            FROM presences pr
            JOIN games g ON g.id = pr.game_id
            WHERE 1=1 {game_where}
            GROUP BY pr.person_id
        ) b ON b.person_id = p.id
    """

    players = query(f"""
        SELECT
            p.id,
            p.name,
            p.nickname,
            COUNT(DISTINCT CASE WHEN e.type = 'goal'         THEN e.id  END) AS goals,
            COUNT(DISTINCT CASE WHEN el.link_type = 'assist' THEN el.id END) AS assists,
            COUNT(DISTINCT pr.game_id) AS appearances,
            COALESCE(t.field_seconds, 0)  AS field_seconds,
            COALESCE(t.gk_seconds,    0)  AS gk_seconds,
            GREATEST(0, COALESCE(b.total_game_seconds, 0)
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
        GROUP BY p.id, p.name, p.nickname,
                 t.field_seconds, t.gk_seconds,
                 b.total_game_seconds,
                 lt.late_seconds, lt.late_arrivals
        HAVING COUNT(DISTINCT pr.game_id) > 0
        ORDER BY goals DESC, assists DESC, appearances DESC
    """)

    years = sorted(set(
        str(g["date"])[:4] for g in all_games if g["date"]
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

    person = query("SELECT * FROM people WHERE id = :id", {"id": person_id})
    if not person:
        return apology("Player not found", 404)
    person = person[0]

    stats = query("""
        SELECT
            COUNT(DISTINCT CASE WHEN e.type = 'goal'         THEN e.id  END) AS goals,
            COUNT(DISTINCT CASE WHEN el.link_type = 'assist' THEN el.id END) AS assists,
            COUNT(DISTINCT pr.game_id) AS appearances
        FROM people p
        LEFT JOIN events e       ON e.person_id         = p.id
        LEFT JOIN event_links el ON el.linked_person_id = p.id
        LEFT JOIN presences pr   ON pr.person_id        = p.id
        WHERE p.id = :person_id
    """, {"person_id": person_id})[0]

    events = query("""
        SELECT e.*, g.title AS game_title, g.date AS game_date, g.youtube_url
        FROM events e
        JOIN games g ON g.id = e.game_id
        WHERE e.person_id = :person_id
        ORDER BY g.date DESC, e.timestamp ASC
    """, {"person_id": person_id})

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
        event["links"] = query("""
            SELECT el.link_type, pe.name
            FROM event_links el
            JOIN people pe ON pe.id = el.linked_person_id
            WHERE el.event_id = :event_id
        """, {"event_id": event["id"]})
        event["youtube_embed"] = build_embed_url(event.get("youtube_url"))

    games = query("""
        SELECT g.id, g.title, g.date, l.name AS location
        FROM presences pr
        JOIN games g     ON g.id  = pr.game_id
        JOIN locations l ON l.id  = g.location_id
        WHERE pr.person_id = :person_id
        ORDER BY g.date DESC
    """, {"person_id": person_id})

    cache = query("""
        SELECT field_seconds, gk_seconds, late_seconds, late_arrivals
        FROM player_stats_cache WHERE person_id = :person_id
    """, {"person_id": person_id})
    field_seconds = cache[0]["field_seconds"] if cache else 0
    gk_seconds    = cache[0]["gk_seconds"]    if cache else 0
    late_seconds  = cache[0]["late_seconds"]  if cache else 0
    late_arrivals = cache[0]["late_arrivals"] if cache else 0

    bench_time = query("""
        SELECT SUM(
            ({secs_end})
            -
            ({secs_arrived})
        ) AS total_game_seconds
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = :person_id
    """.format(
        secs_end=_secs_entered("COALESCE(g.video_end,'01:30:00')"),
        secs_arrived=_secs_entered("COALESCE(pr.arrived_at,'00:00:00')"),
    ), {"person_id": person_id})
    total_game_seconds = bench_time[0]["total_game_seconds"] or 0 if bench_time else 0
    bench_seconds = max(0, total_game_seconds - field_seconds)

    late_by_game = query("""
        SELECT
            pr.game_id,
            pr.arrived_at,
            g.video_start,
            CASE
                WHEN pr.arrived_at IS NOT NULL AND (
                    {secs_arrived} > {secs_start}
                ) THEN {secs_arrived} - {secs_start}
                ELSE NULL
            END AS late_secs
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = :person_id
    """.format(
        secs_arrived=_secs_entered('pr.arrived_at'),
        secs_start=_secs_entered("COALESCE(g.video_start,'00:00:00')"),
    ), {"person_id": person_id})
    late_by_game_map = {r["game_id"]: r["late_secs"] for r in late_by_game}

    gk_by_game = query(f"""
        SELECT game_id, title, date, SUM(gk_seconds) AS gk_seconds
        FROM (
            SELECT g.id AS game_id, g.title, g.date,
                {_secs('re.exited_at')} - {_secs_entered('re.entered_at')} AS gk_seconds
            FROM roster_entries re
            JOIN games g ON g.id = re.game_id
            WHERE re.person_id = :person_id AND re.is_goalkeeper = 1
        ) sub
        GROUP BY game_id, title, date
        ORDER BY date DESC
    """, {"person_id": person_id})

    recent_video = query("""
        SELECT g.id, g.title, g.date, g.youtube_url
        FROM presences pr
        JOIN games g ON g.id = pr.game_id
        WHERE pr.person_id = :person_id AND g.youtube_url IS NOT NULL AND g.youtube_url != ''
        ORDER BY g.date DESC LIMIT 1
    """, {"person_id": person_id})
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

    user = query("SELECT * FROM users WHERE id = :id", {"id": session["user_id"]})[0]

    linked_player = None
    if user["person_id"]:
        row = query("SELECT * FROM people WHERE id = :id", {"id": user["person_id"]})
        if row:
            linked_player = row[0]

    available_players = query("""
        SELECT p.id, p.name, p.nickname
        FROM people p
        LEFT JOIN users u ON u.person_id = p.id
        WHERE u.person_id IS NULL
        ORDER BY p.name
    """)

    if request.method == "GET":
        return render_template("profile.html",
            user=user, linked_player=linked_player, available_players=available_players)

    action = request.form.get("action")

    if action == "link":
        person_id = request.form.get("person_id", "").strip()
        if not person_id:
            return apology("Please select a player")

        conflict = query("SELECT id FROM users WHERE person_id = :person_id", {"person_id": person_id})
        if conflict:
            return apology("This player is already linked to another account")

        execute("UPDATE users SET person_id = :person_id WHERE id = :user_id",
                {"person_id": person_id, "user_id": session["user_id"]})
        session["person_id"] = int(person_id)
        flash("Player profile linked successfully!")

    elif action == "unlink":
        execute("UPDATE users SET person_id = NULL WHERE id = :user_id",
                {"user_id": session["user_id"]})
        session["person_id"] = None
        flash("Player profile unlinked.")

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

        execute("UPDATE users SET hash = :hash WHERE id = :user_id",
                {"hash": generate_password_hash(new_pass), "user_id": session["user_id"]})
        flash("Password changed successfully!")

    return redirect("/profile")


@app.route("/admin")
@login_required
@role_required("admin", "editor")
def admin():
    """Admin dashboard."""
    people_count    = query("SELECT COUNT(*) AS n FROM people")[0]["n"]
    games_count     = query("SELECT COUNT(*) AS n FROM games")[0]["n"]
    locations_count = query("SELECT COUNT(*) AS n FROM locations")[0]["n"]
    users_count     = query("SELECT COUNT(*) AS n FROM users")[0]["n"]
    return render_template("admin/index.html",
        people_count=people_count,
        games_count=games_count,
        locations_count=locations_count,
        users_count=users_count,
    )


# ── People ─────────────────────────────────────────────────────────────────────

@app.route("/admin/people")
@login_required
@role_required("admin", "editor")
def admin_people():
    people = query("""
        SELECT p.*, g.name AS guest_of_name, g.nickname AS guest_of_nickname
        FROM people p
        LEFT JOIN people g ON g.id = p.guest_of
        ORDER BY p.name
    """)
    all_people = query("SELECT id, name, nickname FROM people ORDER BY name")
    return render_template("admin/people.html", people=people, all_people=all_people)


@app.route("/admin/people/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def admin_people_add():
    if request.method == "GET":
        all_people = query("SELECT id, name, nickname FROM people ORDER BY name")
        return render_template("admin/people_form.html", person=None, all_people=all_people)

    name         = request.form.get("name", "").strip()
    nickname     = request.form.get("nickname", "").strip() or None
    phone_number = request.form.get("phone_number", "").strip() or None

    if not name:
        return apology("Name is required")

    try:
        new_id = execute("""
            INSERT INTO people (name, nickname, phone_number)
            VALUES (:name, :nickname, :phone_number)
            RETURNING id
        """, {"name": name, "nickname": nickname, "phone_number": phone_number})
    except Exception:
        return apology("Phone number already exists")

    flash(f"{name} added. You can now upload a photo.")
    return redirect(f"/admin/people/{new_id}/edit")


@app.route("/admin/people/<int:person_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def admin_people_edit(person_id):
    person = query("""
        SELECT p.*, g.name AS guest_of_name, g.nickname AS guest_of_nickname
        FROM people p
        LEFT JOIN people g ON g.id = p.guest_of
        WHERE p.id = :person_id
    """, {"person_id": person_id})
    if not person:
        return apology("Player not found", 404)
    person = person[0]

    if request.method == "GET":
        all_people = query("SELECT id, name, nickname FROM people WHERE id != :id ORDER BY name",
                           {"id": person_id})
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
        execute("""
            UPDATE people
            SET name = :name, nickname = :nickname, phone_number = :phone_number,
                is_in_group_chat = :is_in_group_chat, is_guest = :is_guest, guest_of = :guest_of
            WHERE id = :person_id
        """, {
            "name": name, "nickname": nickname, "phone_number": phone_number,
            "is_in_group_chat": is_in_group_chat, "is_guest": is_guest,
            "guest_of": guest_of, "person_id": person_id,
        })
    except Exception:
        return apology("Phone number already exists")

    flash(f"{name} updated successfully!")
    return redirect("/admin/people")


@app.route("/admin/people/<int:person_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_people_delete(person_id):
    person = query("SELECT * FROM people WHERE id = :id", {"id": person_id})
    if not person:
        return apology("Player not found", 404)
    execute("DELETE FROM people WHERE id = :id", {"id": person_id})
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
    execute("""
        UPDATE people
        SET is_in_group_chat = :is_in_group_chat,
            is_guest         = :is_guest,
            guest_of         = :guest_of
        WHERE id = :person_id
    """, {
        "is_in_group_chat": is_in_group_chat,
        "is_guest": is_guest,
        "guest_of": guest_of,
        "person_id": person_id,
    })
    flash("Player flags updated.")
    return redirect("/admin/people")


# ── Locations ──────────────────────────────────────────────────────────────────

@app.route("/admin/locations")
@login_required
@role_required("admin", "editor")
def admin_locations():
    locations = query("""
        SELECT l.*, COUNT(g.id) AS game_count
        FROM locations l
        LEFT JOIN games g ON g.location_id = l.id
        GROUP BY l.id, l.name
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
        execute("INSERT INTO locations (name) VALUES (:name)", {"name": name})
        flash(f"'{name}' added.")
    except Exception:
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
        execute("UPDATE locations SET name = :name WHERE id = :id", {"name": name, "id": location_id})
        flash(f"Location updated to '{name}'.")
    except Exception:
        flash("Location name already exists.")
    return redirect("/admin/locations")


@app.route("/admin/locations/<int:location_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_locations_delete(location_id):
    try:
        execute("DELETE FROM locations WHERE id = :id", {"id": location_id})
        flash("Location deleted.")
    except Exception:
        flash("Cannot delete — location is used by one or more games.")
    return redirect("/admin/locations")


# ── Games management ───────────────────────────────────────────────────────────

@app.route("/admin/games")
@login_required
@role_required("admin", "editor")
def admin_games():
    games = query("""
        SELECT g.id, g.title, g.date, l.name AS location,
               COUNT(DISTINCT pr.person_id) AS player_count,
               COUNT(DISTINCT e.id) AS event_count
        FROM games g
        JOIN locations l ON l.id = g.location_id
        LEFT JOIN presences pr ON pr.game_id = g.id
        LEFT JOIN events e ON e.game_id = g.id
        GROUP BY g.id, g.title, g.date, l.name
        ORDER BY g.date DESC
    """)
    return render_template("admin/games.html", games=games)


@app.route("/admin/games/<int:game_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def admin_games_edit(game_id):
    game = query("SELECT * FROM games WHERE id = :id", {"id": game_id})
    if not game:
        return apology("Game not found", 404)
    game = game[0]

    locations = query("SELECT * FROM locations ORDER BY name")

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

    execute("""
        UPDATE games SET title = :title, date = :date, location_id = :location_id,
                         youtube_url = :youtube, video_start = :vid_start, video_end = :vid_end
        WHERE id = :game_id
    """, {
        "title": title, "date": date, "location_id": location_id,
        "youtube": youtube, "vid_start": vid_start, "vid_end": vid_end, "game_id": game_id,
    })

    flash("Game updated successfully!")
    return redirect("/admin/games")


@app.route("/admin/games/<int:game_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_games_delete(game_id):
    execute("DELETE FROM games WHERE id = :id", {"id": game_id})
    flash("Game deleted.")
    return redirect("/admin/games")


# ── Users management ───────────────────────────────────────────────────────────

@app.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    users = query("""
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
    execute("UPDATE users SET role = :role WHERE id = :id", {"role": role, "id": user_id})
    flash("Role updated.")
    return redirect("/admin/users")


# ── Photo management ───────────────────────────────────────────────────────────

@app.route("/admin/people/<int:person_id>/photo", methods=["POST"])
@login_required
@role_required("admin")
def admin_upload_photo(person_id):
    """Admin: upload a player photo."""
    person = query("SELECT id FROM people WHERE id = :id", {"id": person_id})
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

    players = query("""
        SELECT id, name, nickname
        FROM people
        WHERE name ILIKE :like OR nickname ILIKE :like
        ORDER BY name
        LIMIT :cap
    """, {"like": like, "cap": cap})

    games = query("""
        SELECT g.id, g.title, g.date, l.name AS location
        FROM games g
        JOIN locations l ON l.id = g.location_id
        WHERE g.title ILIKE :like OR CAST(g.date AS TEXT) ILIKE :like OR l.name ILIKE :like
        ORDER BY g.date DESC
        LIMIT :cap
    """, {"like": like, "cap": cap})

    events = query("""
        SELECT
            e.id,
            e.type,
            e.timestamp,
            e.notes,
            g.id    AS game_id,
            g.title AS game_title,
            g.date  AS game_date,
            pp.id       AS person_id,
            pp.name     AS person_name,
            pp.nickname AS person_nickname,
            lp.id         AS linked_person_id,
            lp.name       AS linked_person_name,
            lp.nickname   AS linked_person_nickname,
            el.link_type,
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
            pp.name    ILIKE :like OR pp.nickname    ILIKE :like
            OR lp.name ILIKE :like OR lp.nickname    ILIKE :like
            OR pon.name ILIKE :like OR pon.nickname  ILIKE :like
            OR poff.name ILIKE :like OR poff.nickname ILIKE :like
            OR e.notes ILIKE :like
            OR g.title ILIKE :like OR CAST(g.date AS TEXT) ILIKE :like
        GROUP BY e.id, e.type, e.timestamp, e.notes, g.id, g.title, g.date,
                 pp.id, pp.name, pp.nickname,
                 lp.id, lp.name, lp.nickname, el.link_type,
                 pon.id, pon.name, pon.nickname,
                 poff.id, poff.name, poff.nickname
        ORDER BY g.date DESC, e.timestamp ASC
        LIMIT :cap
    """, {"like": like, "cap": cap})

    results = {"players": players, "games": games, "events": events}
    total = len(players) + len(games) + len(events)

    return render_template("search.html", q=q, results=results, total=total, too_short=False)