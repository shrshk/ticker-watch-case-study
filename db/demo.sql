--
-- PostgreSQL database dump
--

-- Dumped from database version 16.9
-- Dumped by pg_dump version 16.9

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

ALTER TABLE IF EXISTS ONLY public.watchlists DROP CONSTRAINT IF EXISTS watchlists_user_id_fkey;
ALTER TABLE IF EXISTS ONLY public.watchlist_items DROP CONSTRAINT IF EXISTS watchlist_items_watchlist_id_fkey;
ALTER TABLE IF EXISTS ONLY public.watchlist_items DROP CONSTRAINT IF EXISTS watchlist_items_security_id_fkey;
ALTER TABLE IF EXISTS ONLY public.refresh_tokens DROP CONSTRAINT IF EXISTS refresh_tokens_user_id_fkey;
ALTER TABLE IF EXISTS ONLY public.latest_prices DROP CONSTRAINT IF EXISTS latest_prices_security_id_fkey;
DROP INDEX IF EXISTS public.idx_watchlists_user_name;
DROP INDEX IF EXISTS public.idx_watchlist_items_security;
DROP INDEX IF EXISTS public.idx_securities_ticker_trgm;
DROP INDEX IF EXISTS public.idx_securities_name_trgm;
DROP INDEX IF EXISTS public.idx_refresh_tokens_user;
ALTER TABLE IF EXISTS ONLY public.watchlists DROP CONSTRAINT IF EXISTS watchlists_pkey;
ALTER TABLE IF EXISTS ONLY public.watchlist_items DROP CONSTRAINT IF EXISTS watchlist_items_pkey;
ALTER TABLE IF EXISTS ONLY public.users DROP CONSTRAINT IF EXISTS users_username_key;
ALTER TABLE IF EXISTS ONLY public.users DROP CONSTRAINT IF EXISTS users_pkey;
ALTER TABLE IF EXISTS ONLY public.securities DROP CONSTRAINT IF EXISTS securities_ticker_key;
ALTER TABLE IF EXISTS ONLY public.securities DROP CONSTRAINT IF EXISTS securities_pkey;
ALTER TABLE IF EXISTS ONLY public.refresh_tokens DROP CONSTRAINT IF EXISTS refresh_tokens_token_hash_key;
ALTER TABLE IF EXISTS ONLY public.refresh_tokens DROP CONSTRAINT IF EXISTS refresh_tokens_pkey;
ALTER TABLE IF EXISTS ONLY public.latest_prices DROP CONSTRAINT IF EXISTS latest_prices_pkey;
ALTER TABLE IF EXISTS public.watchlists ALTER COLUMN id DROP DEFAULT;
ALTER TABLE IF EXISTS public.users ALTER COLUMN id DROP DEFAULT;
ALTER TABLE IF EXISTS public.securities ALTER COLUMN id DROP DEFAULT;
ALTER TABLE IF EXISTS public.refresh_tokens ALTER COLUMN id DROP DEFAULT;
DROP SEQUENCE IF EXISTS public.watchlists_id_seq;
DROP TABLE IF EXISTS public.watchlists;
DROP TABLE IF EXISTS public.watchlist_items;
DROP SEQUENCE IF EXISTS public.users_id_seq;
DROP TABLE IF EXISTS public.users;
DROP SEQUENCE IF EXISTS public.securities_id_seq;
DROP TABLE IF EXISTS public.securities;
DROP SEQUENCE IF EXISTS public.refresh_tokens_id_seq;
DROP TABLE IF EXISTS public.refresh_tokens;
DROP TABLE IF EXISTS public.latest_prices;
DROP EXTENSION IF EXISTS pg_trgm;
--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: latest_prices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.latest_prices (
    security_id bigint NOT NULL,
    price double precision NOT NULL,
    effective_at timestamp with time zone NOT NULL,
    source text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refresh_tokens (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone
);


--
-- Name: refresh_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.refresh_tokens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: refresh_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.refresh_tokens_id_seq OWNED BY public.refresh_tokens.id;


--
-- Name: securities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.securities (
    id bigint NOT NULL,
    ticker text NOT NULL,
    name text NOT NULL,
    exchange text,
    asset_type text,
    is_synthetic boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: securities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.securities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: securities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.securities_id_seq OWNED BY public.securities.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    username text NOT NULL,
    email text,
    first_name text DEFAULT ''::text NOT NULL,
    last_name text DEFAULT ''::text NOT NULL,
    password_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
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
-- Name: watchlist_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlist_items (
    watchlist_id bigint NOT NULL,
    security_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: watchlists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlists (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    name text DEFAULT 'default'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: watchlists_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.watchlists_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: watchlists_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.watchlists_id_seq OWNED BY public.watchlists.id;


--
-- Name: refresh_tokens id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens ALTER COLUMN id SET DEFAULT nextval('public.refresh_tokens_id_seq'::regclass);


--
-- Name: securities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.securities ALTER COLUMN id SET DEFAULT nextval('public.securities_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: watchlists id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists ALTER COLUMN id SET DEFAULT nextval('public.watchlists_id_seq'::regclass);


--
-- Data for Name: latest_prices; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.latest_prices (security_id, price, effective_at, source, updated_at) FROM stdin;
1	339.28	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
2	166.85	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
3	102.97	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
4	4.035	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
5	249.41	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
6	269.78	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
7	71.07	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
8	615.92	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
9	393.18	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
10	258.52	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
12	313.77	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
13	201.06	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
14	58.08	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
15	93.28	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
16	1090.82	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
17	43.165	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
18	503	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
19	77.92	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
20	11.92	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
21	816.9	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
22	0.9234	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
23	85.1	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
24	22.93	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
25	33.115	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
26	201.09	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
27	898.22	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
28	236.55	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
29	111.365	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
30	87.45	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
31	203.58	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
32	82.59	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
33	122.18	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
34	104.35	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
35	112.38	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
36	296.15	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
37	116.74	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
38	73.38	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
39	281.01	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
40	13.195	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
41	295.62	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
42	319.42	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
43	64.815	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
44	150.69	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
45	22.795	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
46	350.9	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
47	960.65	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
48	297.54	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
49	123.39	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
50	231.52	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
51	121.99	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
52	304.235	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
53	269.74	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
54	352.16	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
55	87.27	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
56	1165.25	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
57	534.99	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
58	100.96	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
59	15.26	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
60	568.51	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
61	342.89	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
62	13.085	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
63	247.64	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
64	742.34	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
65	165.14	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
66	149.77	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
67	172.88	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
68	501.29	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
69	73.37	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
70	3.665	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
71	36.155	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
72	227.35	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
73	148.44	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
74	129.61	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
75	27.765	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
76	146.17	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
77	212.23	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
78	183.05	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
79	4.935	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
80	52.635	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
81	194.49	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
82	94.925	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
83	137.89	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
84	5.695	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
85	517.345	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
86	25.44	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
87	157.69	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
88	165.27	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
89	375.48	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
90	70.82	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
91	377.78	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
92	94.72	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
93	370.03	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
94	47.72	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
95	30.795	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
96	86.595	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
97	107.445	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
98	158.04	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
99	90.93	2026-09-21 22:55:46.586461+00	api	2026-09-21 22:55:46.600365+00
\.


--
-- Data for Name: securities; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.securities (id, ticker, name, exchange, asset_type, is_synthetic, created_at) FROM stdin;
1	AAPL	Apple	\N	\N	f	2026-09-21 22:55:46.391171+00
2	ABNB	Airbnb	\N	\N	f	2026-09-21 22:55:46.391171+00
3	ABT	Abbott Laboratories	\N	\N	f	2026-09-21 22:55:46.391171+00
4	ACB	Aurora Cannabis	\N	\N	f	2026-09-21 22:55:46.391171+00
5	ADBE	Adobe	\N	\N	f	2026-09-21 22:55:46.391171+00
6	ADP	Automatic Data Processing	\N	\N	f	2026-09-21 22:55:46.391171+00
7	AFRM	Affirm Holdings	\N	\N	f	2026-09-21 22:55:46.391171+00
8	AMD	Advanced Micro Devices	\N	\N	f	2026-09-21 22:55:46.391171+00
9	AMGN	Amgen	\N	\N	f	2026-09-21 22:55:46.391171+00
10	AMZN	Amazon	\N	\N	f	2026-09-21 22:55:46.391171+00
11	ATVI	Activision Blizzard	\N	\N	f	2026-09-21 22:55:46.391171+00
12	AXP	American Express	\N	\N	f	2026-09-21 22:55:46.391171+00
13	BA	Boeing	\N	\N	f	2026-09-21 22:55:46.391171+00
14	BAC	Bank of America	\N	\N	f	2026-09-21 22:55:46.391171+00
15	BBY	Best Buy	\N	\N	f	2026-09-21 22:55:46.391171+00
16	BLK	BlackRock	\N	\N	f	2026-09-21 22:55:46.391171+00
17	BP	BP	\N	\N	f	2026-09-21 22:55:46.391171+00
18	BRK.B	Berkshire Hathaway	\N	\N	f	2026-09-21 22:55:46.391171+00
19	BUD	Anheuser Busch	\N	\N	f	2026-09-21 22:55:46.391171+00
20	BYND	Beyond Meat	\N	\N	f	2026-09-21 22:55:46.391171+00
21	CAT	Caterpillar	\N	\N	f	2026-09-21 22:55:46.391171+00
22	CGC	Canopy Growth	\N	\N	f	2026-09-21 22:55:46.391171+00
23	CLX	Clorox	\N	\N	f	2026-09-21 22:55:46.391171+00
24	CMCSA	Comcast	\N	\N	f	2026-09-21 22:55:46.391171+00
25	CMG	Chipotle	\N	\N	f	2026-09-21 22:55:46.391171+00
26	COIN	Coinbase	\N	\N	f	2026-09-21 22:55:46.391171+00
27	COST	Costco	\N	\N	f	2026-09-21 22:55:46.391171+00
28	CRM	Salesforce	\N	\N	f	2026-09-21 22:55:46.391171+00
29	CSCO	Cisco	\N	\N	f	2026-09-21 22:55:46.391171+00
30	CVS	CVS	\N	\N	f	2026-09-21 22:55:46.391171+00
31	CVX	Chevron	\N	\N	f	2026-09-21 22:55:46.391171+00
32	DAL	Delta Air Lines	\N	\N	f	2026-09-21 22:55:46.391171+00
33	DG	Dollar General	\N	\N	f	2026-09-21 22:55:46.391171+00
34	DIS	Disney	\N	\N	f	2026-09-21 22:55:46.391171+00
35	DLTR	Dollar Tree	\N	\N	f	2026-09-21 22:55:46.391171+00
36	DPZ	Domino's Pizza	\N	\N	f	2026-09-21 22:55:46.391171+00
37	DUK	Duke Energy	\N	\N	f	2026-09-21 22:55:46.391171+00
38	ETSY	Etsy	\N	\N	f	2026-09-21 22:55:46.391171+00
39	EXPE	Expedia	\N	\N	f	2026-09-21 22:55:46.391171+00
40	F	Ford	\N	\N	f	2026-09-21 22:55:46.391171+00
41	FDX	FedEx Corp	\N	\N	f	2026-09-21 22:55:46.391171+00
42	GE	General Electric	\N	\N	f	2026-09-21 22:55:46.391171+00
43	GEHC	GE HealthCare Technologies	\N	\N	f	2026-09-21 22:55:46.391171+00
44	GILD	Gilead	\N	\N	f	2026-09-21 22:55:46.391171+00
45	GME	GameStop	\N	\N	f	2026-09-21 22:55:46.391171+00
46	GOOG	Alphabet	\N	\N	f	2026-09-21 22:55:46.391171+00
47	GS	Goldman Sachs	\N	\N	f	2026-09-21 22:55:46.391171+00
48	HD	Home Depot	\N	\N	f	2026-09-21 22:55:46.391171+00
49	HOOD	Robinhood	\N	\N	f	2026-09-21 22:55:46.391171+00
50	IBM	IBM	\N	\N	f	2026-09-21 22:55:46.391171+00
51	INTC	Intel	\N	\N	f	2026-09-21 22:55:46.391171+00
52	INTU	Intuit	\N	\N	f	2026-09-21 22:55:46.391171+00
53	JNJ	Johnson & Johnson	\N	\N	f	2026-09-21 22:55:46.391171+00
54	JPM	JPMorgan Chase	\N	\N	f	2026-09-21 22:55:46.391171+00
55	KO	Coca-Cola	\N	\N	f	2026-09-21 22:55:46.391171+00
56	LLY	Eli Lilly & Co	\N	\N	f	2026-09-21 22:55:46.391171+00
57	LMT	Lockheed Martin	\N	\N	f	2026-09-21 22:55:46.391171+00
58	LULU	Lululemon	\N	\N	f	2026-09-21 22:55:46.391171+00
59	LYFT	Lyft	\N	\N	f	2026-09-21 22:55:46.391171+00
60	MA	Mastercard	\N	\N	f	2026-09-21 22:55:46.391171+00
61	MAR	Marriott	\N	\N	f	2026-09-21 22:55:46.391171+00
62	MAT	Mattel	\N	\N	f	2026-09-21 22:55:46.391171+00
63	MCD	McDonald's	\N	\N	f	2026-09-21 22:55:46.391171+00
64	META	Meta Platforms	\N	\N	f	2026-09-21 22:55:46.391171+00
65	MMM	3M	\N	\N	f	2026-09-21 22:55:46.391171+00
66	MRK	Merck & Co	\N	\N	f	2026-09-21 22:55:46.391171+00
67	MRNA	Moderna	\N	\N	f	2026-09-21 22:55:46.391171+00
68	MSFT	Microsoft	\N	\N	f	2026-09-21 22:55:46.391171+00
69	NFLX	Netflix	\N	\N	f	2026-09-21 22:55:46.391171+00
70	NIO	Nio	\N	\N	f	2026-09-21 22:55:46.391171+00
71	NKE	Nike	\N	\N	f	2026-09-21 22:55:46.391171+00
72	NVDA	NVIDIA	\N	\N	f	2026-09-21 22:55:46.391171+00
73	ORCL	Oracle	\N	\N	f	2026-09-21 22:55:46.391171+00
74	PEP	PepsiCo	\N	\N	f	2026-09-21 22:55:46.391171+00
75	PFE	Pfizer	\N	\N	f	2026-09-21 22:55:46.391171+00
76	PG	Procter & Gamble	\N	\N	f	2026-09-21 22:55:46.391171+00
77	PGR	Progressive	\N	\N	f	2026-09-21 22:55:46.391171+00
78	PLTR	Palantir Technologies	\N	\N	f	2026-09-21 22:55:46.391171+00
79	PTON	Peloton	\N	\N	f	2026-09-21 22:55:46.391171+00
80	PYPL	Paypal	\N	\N	f	2026-09-21 22:55:46.391171+00
81	RTX	Raytheon Technologies Corp	\N	\N	f	2026-09-21 22:55:46.391171+00
82	SBUX	Starbucks	\N	\N	f	2026-09-21 22:55:46.391171+00
83	SHOP	Shopify	\N	\N	f	2026-09-21 22:55:46.391171+00
84	SNAP	Snap	\N	\N	f	2026-09-21 22:55:46.391171+00
85	SPOT	Spotify	\N	\N	f	2026-09-21 22:55:46.391171+00
86	T	AT&T	\N	\N	f	2026-09-21 22:55:46.391171+00
87	TGT	Target	\N	\N	f	2026-09-21 22:55:46.391171+00
88	TMUS	T-Mobile	\N	\N	f	2026-09-21 22:55:46.391171+00
89	TSLA	Tesla	\N	\N	f	2026-09-21 22:55:46.391171+00
90	UBER	UBER	\N	\N	f	2026-09-21 22:55:46.391171+00
91	UNH	UnitedHealth Group	\N	\N	f	2026-09-21 22:55:46.391171+00
92	UPS	United Parcel Service	\N	\N	f	2026-09-21 22:55:46.391171+00
93	V	Visa	\N	\N	f	2026-09-21 22:55:46.391171+00
94	VZ	Verizon	\N	\N	f	2026-09-21 22:55:46.391171+00
95	WBD	Warner Bros. Discovery	\N	\N	f	2026-09-21 22:55:46.391171+00
96	WFC	Wells Fargo	\N	\N	f	2026-09-21 22:55:46.391171+00
97	WMT	Walmart	\N	\N	f	2026-09-21 22:55:46.391171+00
98	XOM	ExxonMobil Holdings Corporation	\N	\N	f	2026-09-21 22:55:46.391171+00
99	ZM	Zoom	\N	\N	f	2026-09-21 22:55:46.391171+00
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.users (id, username, email, first_name, last_name, password_hash, created_at) FROM stdin;
1	user1	user1@casestudy.com	User	One	$2b$12$MuwAa9VD8N1qTg9eAUbwUONeH7L6nH21xZLKm82Prh54LMdYJM.Fm	2026-09-21 22:55:45.950546+00
2	user2	user2@casestudy.com	User	Two	$2b$12$fNjEXHSdqNB8CR5Zgb3em.TF49RkDlG3OXDu2YsrLpL4D1utJt3Hy	2026-09-21 22:55:46.196239+00
\.


--
-- Data for Name: watchlist_items; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.watchlist_items (watchlist_id, security_id, created_at) FROM stdin;
1	72	2026-09-21 22:56:15.601821+00
1	1	2026-09-21 22:56:15.637258+00
1	89	2026-09-21 22:56:15.668916+00
1	68	2026-09-21 22:56:15.7001+00
1	10	2026-09-21 22:56:15.730301+00
1	64	2026-09-21 22:56:15.762004+00
2	46	2026-09-21 22:56:16.0774+00
2	8	2026-09-21 22:56:16.111647+00
2	26	2026-09-21 22:56:16.141598+00
\.


--
-- Data for Name: watchlists; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.watchlists (id, user_id, name, created_at) FROM stdin;
1	1	default	2026-09-21 22:55:45.950546+00
2	2	default	2026-09-21 22:55:46.196239+00
\.


--
-- Name: refresh_tokens_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.refresh_tokens_id_seq', 4, true);


--
-- Name: securities_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.securities_id_seq', 99, true);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.users_id_seq', 2, true);


--
-- Name: watchlists_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.watchlists_id_seq', 2, true);


--
-- Name: latest_prices latest_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.latest_prices
    ADD CONSTRAINT latest_prices_pkey PRIMARY KEY (security_id);


--
-- Name: refresh_tokens refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id);


--
-- Name: refresh_tokens refresh_tokens_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: securities securities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.securities
    ADD CONSTRAINT securities_pkey PRIMARY KEY (id);


--
-- Name: securities securities_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.securities
    ADD CONSTRAINT securities_ticker_key UNIQUE (ticker);


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
-- Name: watchlist_items watchlist_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_pkey PRIMARY KEY (watchlist_id, security_id);


--
-- Name: watchlists watchlists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_pkey PRIMARY KEY (id);


--
-- Name: idx_refresh_tokens_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refresh_tokens_user ON public.refresh_tokens USING btree (user_id);


--
-- Name: idx_securities_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_securities_name_trgm ON public.securities USING gin (name public.gin_trgm_ops);


--
-- Name: idx_securities_ticker_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_securities_ticker_trgm ON public.securities USING gin (ticker public.gin_trgm_ops);


--
-- Name: idx_watchlist_items_security; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlist_items_security ON public.watchlist_items USING btree (security_id);


--
-- Name: idx_watchlists_user_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_watchlists_user_name ON public.watchlists USING btree (user_id, name);


--
-- Name: latest_prices latest_prices_security_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.latest_prices
    ADD CONSTRAINT latest_prices_security_id_fkey FOREIGN KEY (security_id) REFERENCES public.securities(id) ON DELETE CASCADE;


--
-- Name: refresh_tokens refresh_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: watchlist_items watchlist_items_security_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_security_id_fkey FOREIGN KEY (security_id) REFERENCES public.securities(id) ON DELETE CASCADE;


--
-- Name: watchlist_items watchlist_items_watchlist_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES public.watchlists(id) ON DELETE CASCADE;


--
-- Name: watchlists watchlists_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

