-- Dumped from database version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: event_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_links (
    id integer NOT NULL,
    event_id integer NOT NULL,
    linked_person_id integer NOT NULL,
    link_type text NOT NULL
);


--
-- Name: event_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_links_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_links_id_seq OWNED BY public.event_links.id;


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id integer NOT NULL,
    game_id integer NOT NULL,
    person_id integer,
    type text NOT NULL,
    "timestamp" text NOT NULL,
    duration integer DEFAULT 20 NOT NULL,
    notes text,
    CONSTRAINT events_duration_check CHECK ((duration > 0)),
    CONSTRAINT events_timestamp_check CHECK (("timestamp" ~ '^\d{2}:\d{2}:\d{2}$'::text))
);


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;


--
-- Name: flask_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flask_sessions (
    id integer NOT NULL,
    session_id text,
    data bytea,
    expiry timestamp without time zone
);


--
-- Name: flask_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.flask_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: flask_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.flask_sessions_id_seq OWNED BY public.flask_sessions.id;


--
-- Name: games; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.games (
    id integer NOT NULL,
    title text NOT NULL,
    date date NOT NULL,
    youtube_url text,
    location_id integer NOT NULL,
    video_start text,
    video_end text,
    CONSTRAINT games_video_end_check CHECK ((video_end ~ '^\d{2}:\d{2}:\d{2}$'::text)),
    CONSTRAINT games_video_start_check CHECK ((video_start ~ '^\d{2}:\d{2}:\d{2}$'::text))
);


--
-- Name: games_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.games_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: games_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.games_id_seq OWNED BY public.games.id;


--
-- Name: locations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.locations (
    id integer NOT NULL,
    name text NOT NULL
);


--
-- Name: locations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.locations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: locations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.locations_id_seq OWNED BY public.locations.id;


--
-- Name: people; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.people (
    id integer NOT NULL,
    name text NOT NULL,
    nickname text,
    phone_number text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    is_in_group_chat integer DEFAULT 0 NOT NULL,
    is_guest integer DEFAULT 0 NOT NULL,
    guest_of integer
);


--
-- Name: people_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.people_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: people_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.people_id_seq OWNED BY public.people.id;


--
-- Name: player_stats_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.player_stats_cache (
    person_id integer NOT NULL,
    field_seconds integer DEFAULT 0 NOT NULL,
    gk_seconds integer DEFAULT 0 NOT NULL,
    updated_at timestamp without time zone DEFAULT now(),
    late_seconds integer DEFAULT 0 NOT NULL,
    late_arrivals integer DEFAULT 0 NOT NULL
);


--
-- Name: presences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.presences (
    id integer NOT NULL,
    game_id integer NOT NULL,
    person_id integer NOT NULL,
    arrived_at text
);


--
-- Name: presences_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.presences_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: presences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.presences_id_seq OWNED BY public.presences.id;


--
-- Name: roster_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roster_entries (
    id integer NOT NULL,
    game_id integer NOT NULL,
    person_id integer NOT NULL,
    team_id integer NOT NULL,
    entered_at text NOT NULL,
    exited_at text,
    is_goalkeeper integer DEFAULT 0 NOT NULL,
    CONSTRAINT roster_entries_entered_at_check CHECK ((entered_at ~ '^\d{2}:\d{2}:\d{2}$'::text)),
    CONSTRAINT roster_entries_exited_at_check CHECK ((exited_at ~ '^\d{2}:\d{2}:\d{2}$'::text))
);


--
-- Name: roster_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.roster_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: roster_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.roster_entries_id_seq OWNED BY public.roster_entries.id;


--
-- Name: roster_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roster_snapshots (
    id integer NOT NULL,
    game_id integer NOT NULL,
    valid_from text NOT NULL,
    team_id integer NOT NULL,
    person_id integer NOT NULL,
    is_goalkeeper integer DEFAULT 0 NOT NULL
);


--
-- Name: roster_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.roster_snapshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: roster_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.roster_snapshots_id_seq OWNED BY public.roster_snapshots.id;


--
-- Name: segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segments (
    id integer NOT NULL,
    game_id integer NOT NULL,
    team_a_id integer NOT NULL,
    team_b_id integer NOT NULL,
    started_at text NOT NULL,
    ended_at text
);


--
-- Name: segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.segments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: segments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.segments_id_seq OWNED BY public.segments.id;


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id integer NOT NULL,
    session_id character varying(255),
    data bytea,
    expiry timestamp without time zone
);


--
-- Name: sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sessions_id_seq OWNED BY public.sessions.id;


--
-- Name: substitution_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.substitution_details (
    id integer NOT NULL,
    event_id integer NOT NULL,
    player_off_id integer NOT NULL,
    player_on_id integer NOT NULL,
    team_id integer NOT NULL,
    is_goalkeeper_swap integer DEFAULT 0 NOT NULL
);


--
-- Name: substitution_details_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.substitution_details_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: substitution_details_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.substitution_details_id_seq OWNED BY public.substitution_details.id;


--
-- Name: team_change_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_change_details (
    id integer NOT NULL,
    event_id integer NOT NULL,
    leaving_team_id integer NOT NULL,
    entering_team_id integer NOT NULL,
    staying_team_id integer
);


--
-- Name: team_change_details_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.team_change_details_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: team_change_details_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.team_change_details_id_seq OWNED BY public.team_change_details.id;


--
-- Name: teams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams (
    id integer NOT NULL,
    game_id integer NOT NULL,
    color text NOT NULL
);


--
-- Name: teams_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.teams_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: teams_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.teams_id_seq OWNED BY public.teams.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username text NOT NULL,
    email text,
    hash text NOT NULL,
    role text DEFAULT 'viewer'::text NOT NULL,
    person_id integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'editor'::text, 'viewer'::text])))
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: whatsapp_list; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whatsapp_list (
    id integer NOT NULL,
    game_id integer NOT NULL,
    person_id integer NOT NULL,
    "timestamp" text
);


--
-- Name: whatsapp_list_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.whatsapp_list_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: whatsapp_list_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.whatsapp_list_id_seq OWNED BY public.whatsapp_list.id;


--
-- Name: event_links id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_links ALTER COLUMN id SET DEFAULT nextval('public.event_links_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);


--
-- Name: flask_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flask_sessions ALTER COLUMN id SET DEFAULT nextval('public.flask_sessions_id_seq'::regclass);


--
-- Name: games id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.games ALTER COLUMN id SET DEFAULT nextval('public.games_id_seq'::regclass);


--
-- Name: locations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locations ALTER COLUMN id SET DEFAULT nextval('public.locations_id_seq'::regclass);


--
-- Name: people id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people ALTER COLUMN id SET DEFAULT nextval('public.people_id_seq'::regclass);


--
-- Name: presences id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.presences ALTER COLUMN id SET DEFAULT nextval('public.presences_id_seq'::regclass);


--
-- Name: roster_entries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_entries ALTER COLUMN id SET DEFAULT nextval('public.roster_entries_id_seq'::regclass);


--
-- Name: roster_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_snapshots ALTER COLUMN id SET DEFAULT nextval('public.roster_snapshots_id_seq'::regclass);


--
-- Name: segments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments ALTER COLUMN id SET DEFAULT nextval('public.segments_id_seq'::regclass);


--
-- Name: sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions ALTER COLUMN id SET DEFAULT nextval('public.sessions_id_seq'::regclass);


--
-- Name: substitution_details id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.substitution_details ALTER COLUMN id SET DEFAULT nextval('public.substitution_details_id_seq'::regclass);


--
-- Name: team_change_details id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_change_details ALTER COLUMN id SET DEFAULT nextval('public.team_change_details_id_seq'::regclass);


--
-- Name: teams id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams ALTER COLUMN id SET DEFAULT nextval('public.teams_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: whatsapp_list id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_list ALTER COLUMN id SET DEFAULT nextval('public.whatsapp_list_id_seq'::regclass);


--
-- Name: event_links event_links_event_id_linked_person_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_links
    ADD CONSTRAINT event_links_event_id_linked_person_id_key UNIQUE (event_id, linked_person_id);


--
-- Name: event_links event_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_links
    ADD CONSTRAINT event_links_pkey PRIMARY KEY (id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: flask_sessions flask_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flask_sessions
    ADD CONSTRAINT flask_sessions_pkey PRIMARY KEY (id);


--
-- Name: flask_sessions flask_sessions_session_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flask_sessions
    ADD CONSTRAINT flask_sessions_session_id_key UNIQUE (session_id);


--
-- Name: games games_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.games
    ADD CONSTRAINT games_pkey PRIMARY KEY (id);


--
-- Name: locations locations_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_name_key UNIQUE (name);


--
-- Name: locations locations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_pkey PRIMARY KEY (id);


--
-- Name: people people_phone_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_phone_number_key UNIQUE (phone_number);


--
-- Name: people people_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_pkey PRIMARY KEY (id);


--
-- Name: player_stats_cache player_stats_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.player_stats_cache
    ADD CONSTRAINT player_stats_cache_pkey PRIMARY KEY (person_id);


--
-- Name: presences presences_game_id_person_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.presences
    ADD CONSTRAINT presences_game_id_person_id_key UNIQUE (game_id, person_id);


--
-- Name: presences presences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.presences
    ADD CONSTRAINT presences_pkey PRIMARY KEY (id);


--
-- Name: roster_entries roster_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_entries
    ADD CONSTRAINT roster_entries_pkey PRIMARY KEY (id);


--
-- Name: roster_snapshots roster_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_snapshots
    ADD CONSTRAINT roster_snapshots_pkey PRIMARY KEY (id);


--
-- Name: segments segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_session_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_session_id_key UNIQUE (session_id);


--
-- Name: substitution_details substitution_details_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.substitution_details
    ADD CONSTRAINT substitution_details_event_id_key UNIQUE (event_id);


--
-- Name: substitution_details substitution_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.substitution_details
    ADD CONSTRAINT substitution_details_pkey PRIMARY KEY (id);


--
-- Name: team_change_details team_change_details_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_change_details
    ADD CONSTRAINT team_change_details_event_id_key UNIQUE (event_id);


--
-- Name: team_change_details team_change_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_change_details
    ADD CONSTRAINT team_change_details_pkey PRIMARY KEY (id);


--
-- Name: teams teams_game_id_color_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_game_id_color_key UNIQUE (game_id, color);


--
-- Name: teams teams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_person_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_person_id_key UNIQUE (person_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: whatsapp_list whatsapp_list_game_id_person_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_list
    ADD CONSTRAINT whatsapp_list_game_id_person_id_key UNIQUE (game_id, person_id);


--
-- Name: whatsapp_list whatsapp_list_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_list
    ADD CONSTRAINT whatsapp_list_pkey PRIMARY KEY (id);


--
-- Name: idx_event_links_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_links_event_id ON public.event_links USING btree (event_id);


--
-- Name: idx_event_links_linked_person_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_links_linked_person_id ON public.event_links USING btree (linked_person_id);


--
-- Name: idx_games_location_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_games_location_id ON public.games USING btree (location_id);


--
-- Name: idx_presences_game_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_presences_game_id ON public.presences USING btree (game_id);


--
-- Name: idx_presences_person_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_presences_person_id ON public.presences USING btree (person_id);


--
-- Name: idx_roster_game_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_roster_game_id ON public.roster_entries USING btree (game_id);


--
-- Name: idx_roster_person_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_roster_person_id ON public.roster_entries USING btree (person_id);


--
-- Name: idx_roster_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_roster_team_id ON public.roster_entries USING btree (team_id);


--
-- Name: idx_segments_game_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_segments_game_id ON public.segments USING btree (game_id);


--
-- Name: idx_snapshots_game_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshots_game_time ON public.roster_snapshots USING btree (game_id, valid_from);


--
-- Name: idx_teams_game_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_teams_game_id ON public.teams USING btree (game_id);


--
-- Name: idx_whatsapp_list_game_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whatsapp_list_game_id ON public.whatsapp_list USING btree (game_id);


--
-- Name: idx_whatsapp_list_person_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whatsapp_list_person_id ON public.whatsapp_list USING btree (person_id);


--
-- Name: people people_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER people_updated_at BEFORE UPDATE ON public.people FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: users users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: event_links event_links_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_links
    ADD CONSTRAINT event_links_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: event_links event_links_linked_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_links
    ADD CONSTRAINT event_links_linked_person_id_fkey FOREIGN KEY (linked_person_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: events events_game_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_game_id_fkey FOREIGN KEY (game_id) REFERENCES public.games(id) ON DELETE CASCADE;


--
-- Name: events events_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id);


--
-- Name: games games_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.games
    ADD CONSTRAINT games_location_id_fkey FOREIGN KEY (location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--
-- Name: people people_guest_of_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_guest_of_fkey FOREIGN KEY (guest_of) REFERENCES public.people(id) ON DELETE SET NULL;


--
-- Name: player_stats_cache player_stats_cache_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.player_stats_cache
    ADD CONSTRAINT player_stats_cache_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: presences presences_game_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.presences
    ADD CONSTRAINT presences_game_id_fkey FOREIGN KEY (game_id) REFERENCES public.games(id) ON DELETE CASCADE;


--
-- Name: presences presences_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.presences
    ADD CONSTRAINT presences_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: roster_entries roster_entries_game_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_entries
    ADD CONSTRAINT roster_entries_game_id_fkey FOREIGN KEY (game_id) REFERENCES public.games(id) ON DELETE CASCADE;


--
-- Name: roster_entries roster_entries_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_entries
    ADD CONSTRAINT roster_entries_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: roster_entries roster_entries_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_entries
    ADD CONSTRAINT roster_entries_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: roster_snapshots roster_snapshots_game_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_snapshots
    ADD CONSTRAINT roster_snapshots_game_id_fkey FOREIGN KEY (game_id) REFERENCES public.games(id) ON DELETE CASCADE;


--
-- Name: roster_snapshots roster_snapshots_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_snapshots
    ADD CONSTRAINT roster_snapshots_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: roster_snapshots roster_snapshots_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roster_snapshots
    ADD CONSTRAINT roster_snapshots_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: segments segments_game_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_game_id_fkey FOREIGN KEY (game_id) REFERENCES public.games(id) ON DELETE CASCADE;


--
-- Name: segments segments_team_a_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_team_a_id_fkey FOREIGN KEY (team_a_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: segments segments_team_b_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_team_b_id_fkey FOREIGN KEY (team_b_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: substitution_details substitution_details_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.substitution_details
    ADD CONSTRAINT substitution_details_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: substitution_details substitution_details_player_off_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.substitution_details
    ADD CONSTRAINT substitution_details_player_off_id_fkey FOREIGN KEY (player_off_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: substitution_details substitution_details_player_on_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.substitution_details
    ADD CONSTRAINT substitution_details_player_on_id_fkey FOREIGN KEY (player_on_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: substitution_details substitution_details_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.substitution_details
    ADD CONSTRAINT substitution_details_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_change_details team_change_details_entering_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_change_details
    ADD CONSTRAINT team_change_details_entering_team_id_fkey FOREIGN KEY (entering_team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_change_details team_change_details_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_change_details
    ADD CONSTRAINT team_change_details_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: team_change_details team_change_details_leaving_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_change_details
    ADD CONSTRAINT team_change_details_leaving_team_id_fkey FOREIGN KEY (leaving_team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_change_details team_change_details_staying_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_change_details
    ADD CONSTRAINT team_change_details_staying_team_id_fkey FOREIGN KEY (staying_team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: teams teams_game_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_game_id_fkey FOREIGN KEY (game_id) REFERENCES public.games(id) ON DELETE CASCADE;


--
-- Name: users users_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id) ON DELETE SET NULL;


--
-- Name: whatsapp_list whatsapp_list_game_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_list
    ADD CONSTRAINT whatsapp_list_game_id_fkey FOREIGN KEY (game_id) REFERENCES public.games(id) ON DELETE CASCADE;


--
-- Name: whatsapp_list whatsapp_list_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_list
    ADD CONSTRAINT whatsapp_list_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id) ON DELETE CASCADE;
