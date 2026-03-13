# Arnica F.Q. - Football Match Tracker

#### Video Demo: https://www.youtube.com/watch?v=ObAuJqqad9U

#### Description:

Arnica F.Q. is a full-stack web application built to track everything that happens in a recreational football group: who played, who scored, how long each player was on the field, on the goal and on the bench, and when substitutions happened. It is built with Python, SQL, HTML, and Javascript, using Flask, SQLite3, and Bootstrap 5. It integrates with YouTube to let users watch match footage directly on the platform while simultaneously seeing a live roster sidebar that updates as the video plays.

The project was born from a real need: a group of friends who play football regularly and record their matches on video, but had no good way to keep statistics, review highlights, or know who spent most of the game on the bench. The application is designed to solve these issues in one place.

---

## Core Features

**Game Registration (6-step flow).** Creating a game walks through a structured wizard:
1. Game info and YouTube URL
2. Teams color setup
3. Attendance (WhatsApp list)
4. Presences (who actually showed up, with optional late-arrival timestamps)
5. Roster assignment
6. Event logging.

This stepwise approach avoids the complexity of a single large form and guides editors through every piece of information needed.

**Live Roster Sidebar.** On each game's page, a sidebar shows which players are currently on the field and which are on the bench - synchronized to the YouTube video as it plays. This is powered by a materialized `roster_snapshots` table which pre-computes the full player timeline at every substitution boundary. A binary search in JavaScript finds the correct snapshot for the current video timestamp, and the sidebar updates every 3 seconds automatically. Player entries animate in green when they enter and slide out in red when they leave, making substitutions visually clear. (This is one of the most interesting features!!! **I'm proud of it** hehe)

**Event Timeline.** Every game has a timeline of events - goals, assists, substitutions, highlights, injuries, and fouls - each stamped with a video timestamp. Clicking any timestamp seeks the YouTube player to that exact moment. Events are filterable by type.

**Player Profiles and Leaderboard.** Each player has a profile page showing their goals, assists, and time statistics broken down per game. The leaderboard ranks all players and can be filtered by game or time period (year/quarter). Time statistics include field time, goalkeeper time, and bench time - all computed from roster entries. Field time counts all on-field seconds regardless of role; goalkeeper time is a subset of field time; bench time is derived from `video_end − arrived_at − field_seconds`, so players who arrived late are not registered with false bench time.

**Search.** A global search bar in the navbar queries across players (name, nickname), games (title, date, location), and all event types. Event results match on every participant - the scorer, the assisting player, both players in a substitution, the fouled player, and the event's notes. Each event result links directly to the game page.

---

## File Structure

**`arnica.db`** Is the database. It has tables: `people`, `users`, `games`, `locations`, `teams`, `roster_entries`, `roster_snapshots`, `presences`, `events`, `event_links`, `substitution_details`, `whatsapp_list`, and `player_stats_cache`. Foreign key relationships enforce data integrity throughout. The DB schema is visible at /schema route.


**`helpers.py`** defines helper functions (although it was not used very often)

**`app.py`** holds the entire app backend by itself (I need to break up in multiple files later...).

It has multiple routes:

**`games/id`** handles the main game view. It fetches events, roster segments, and the full materialized roster timeline, constructs the YouTube embed URL and renders game.html.

**`game.html`** is the most complex template. It renders the event timeline, the live roster sidebar, and embeds the YouTube player. The YouTube IFrame API is initialized here. A `timeToSecs` utility and binary search over `rosterTimeline.timestamps` keep the sidebar accurate as the video plays.

**`/game/id/events` and `events.html`** handle event creation, editing, and deletion. Substitutions create two `roster_entries` - one closing the departing player's entry and one opening the incoming player's entry - and trigger a `roster_snapshots` rebuild. The events page also shows a live roster sidebar using the same diff-based animation logic as the game page.

**`/leaderboard`**
For filtered views (by game or period) it computes time statistics on-the-fly using a layered SQL query with per-entry duration caps and per-game total caps to prevent overcounting.

**`/player/id`**
Shows statistics of the player. For the all-time view it reads from `player_stats_cache` for speed.

**`helper player_stats_cache`** recomputes cached field and GK time for a single player or all players. It iterates over their roster entries, caps each duration at `game_end − entered_at`, and upserts the result. This cache is invalidated whenever roster entries change.

**`helper roster_snapshots`** materializes the roster at every substitution boundary for a game. Each snapshot records exactly who is on which team and in what role at that moment in video time, enabling the O(log n) binary search in the frontend.

**`/presences` and `presences.html`** manage who attended each game. Each checked player can optionally have an `arrived_at` video timestamp, which is used downstream to correctly calculate bench time - a player who arrived 30 minutes into the game cannot have 30 minutes of bench time attributed to the period before they even arrived.

**`/search` and `search.html`** implement global search. The SQL query for events uses multiple LEFT JOINs to match across all participants in every event type, with `GROUP BY e.id` to deduplicate. Results are grouped by section and each event renders a human-readable description appropriate to its type.

**`admin_routes`** and the admin templates manage players, users, games, and locations. The player edit form (`admin_people_form.html`) includes toggles for `is_in_group_chat`, `is_guest`, and a dropdown (showing nicknames) for `guest_of` - tracking which regular member brought a guest to a game.

**`styles.css`** contains custom overrides on top of Bootstrap: the red brand color, avatar styling, roster sidebar animation keyframes (`playerIn`, `playerOut`), and minor layout tweaks.

---

## Design Decisions

**DB Schema**  At the beginning, everything was tracked through the `events` table. After logging one entire game it became clear this was the wrong approach for tracking roster state and player time statistics — every read required an expensive search and recalculation over the full event history. The solution was to introduce pre-computed, effectively read-only tables: `roster_snapshots` materializes the full roster at every substitution boundary, and `player_stats_cache` stores each player's accumulated field, goalkeeper, and bench seconds. The heavy computation runs only when data changes (on substitutions or roster edits), not on every page load.

**Materialized snapshots over live calculation.** An early design computed the roster at any video timestamp by replaying all roster entries up to that point. This was correct but slow for long games with many substitutions. Pre-materializing snapshots at substitution boundaries means the frontend only needs a binary search, which is fast enough to run on every video poll cycle.

**`arrived_at` on presences rather than roster entries.** Bench time is derived from total available game time minus field time. If a player arrived late, their "available time" should start from their arrival, not from `00:00:00`. Storing `arrived_at` on the `presences` table (rather than implying it from the first roster entry) keeps the concept clean and allows players who arrived late but went straight onto the field to still have zero bench time correctly.
