# LEGO Set Inventory & Pricing APIs — Research Report

**Prepared for:** jay  
**Date:** 2026-04-21  
**Goal:** Identify public websites, APIs, and services that can take an identifier printed on the outside of a LEGO box (set number, UPC/EAN barcode, or name) and return structured metadata plus current resale/market price data — with an eye toward building a smart inventory app.  
**Scope:** Free and freemium services only. Both sealed (new) and used/parted-out pricing in scope. Developer-focused: auth methods, endpoints, rate limits, and example requests are included so this can feed directly into app design.

---

## TL;DR — Chosen Stack

Three API integrations plus a local catalog mirror, deployed as a Python app on a Mac mini with a SwiftUI iPhone client for scan/photo input. No single service covers everything for free, so the chosen approach combines them and moves the heaviest catalog queries off the network entirely (via the Rebrickable CSV mirror). See §7 for the full architecture and §10 for the decision summary.

1. **Identification layer — Rebrickable** (free API key, generous free tier) for canonical set metadata, parts lists, minifigs, and images. Indexed by `set_num` like `10294-1`.
2. **Pricing layer — Brickset + BrickLink Price Guide** for hard numbers.
   - **Brickset** exposes `USPrice`, `UKPrice`, `USRetailPrice`, and current/historical average sealed resale price fields, plus UPC/EAN — free API key after approval.
   - **BrickLink Price Guide** (OAuth1 consumer key + token, free) gives granular sold-listing stats for sets and parts, both new and used, with 6-month windows — the gold standard for parted-out and used values.
3. **Barcode layer — Brickset first, UPCitemdb second.**
   - Brickset stores the official EAN/UPC for each set; if the scanned barcode matches, you get the set number directly.
   - If Brickset doesn't have the code (rare for sets, common for polybags and regional variants), fall back to **UPCitemdb's free "Explorer" tier** (100 lookups/day, no signup) to at least resolve the box to a product name you can search.

Nice-to-haves:

- **Brickognize API** — image-recognition for sets/minifigs/parts. Useful when the box barcode is damaged or you just have a photo. Free, anonymous, rate-limited.
- **PriceCharting** — has the cleanest eBay-sold price guide (Loose/CIB/New/Graded) but API access is paywalled. Worth mentioning because it is the single best sealed-market signal if you outgrow the free tier.
- **BrickEconomy** — excellent consumer site for at-a-glance current market value. API exists and is documented, but pricing plans are not clearly free — treat as paid.

```
   ┌─────────────────────────────────────────┐
   │ Box in hand                             │
   └───────────┬─────────────────────────────┘
               │ OCR / barcode scan / manual
               ▼
     ┌─────────────────────┐        ┌──────────────────────┐
     │ Barcode (UPC/EAN)?  │──yes──►│ Brickset getSets     │
     └──────────┬──────────┘        │ {query: "<barcode>"} │
                │ no/miss            └───────────┬──────────┘
                ▼                                │
     ┌─────────────────────┐                    ▼
     │ Set number known?   │──────────► canonical set_num (e.g. 10294-1)
     └──────────┬──────────┘                    │
                │ no                            │
                ▼                               │
     ┌─────────────────────┐                    │
     │ Brickognize image   │────────────────────┘
     │ recognition API     │
     └─────────────────────┘
                                                ▼
     ┌──────────────────────────────────────────────────────┐
     │ Rebrickable /lego/sets/{set_num}/  — metadata+parts  │
     │ Brickset  getSets                  — price + UPC     │
     │ BrickLink Price Guide              — used / parted   │
     └──────────────────────────────────────────────────────┘
                                                ▼
                                     Your inventory DB
```

---

## 1. Identification Layer

This is the layer that turns whatever you scanned or typed into a canonical `set_num` and fetches the authoritative catalog record (name, theme, year, pieces, image, minifigs, parts).

### 1.1 Rebrickable — primary catalog

- **Base URL:** `https://rebrickable.com/api/v3/`
- **Auth:** API key header — `Authorization: key YOUR_KEY` (note the literal word `key`, not `Bearer`). Query-string form `?key=...` also works but header is preferred.
- **Cost:** Free. Generate one key per account at your profile settings page.
- **Rate limit:** Rebrickable documents that the API is throttled and explicitly asks you not to call it for every part in bulk operations — download the CSV dumps for that. Pagination defaults to 100, max 1000 per page.
- **Key endpoints you'll hit:**
  - `GET /lego/sets/?search=<name>` — search by name, partial set number, etc.
  - `GET /lego/sets/{set_num}/` — full set metadata (name, year, theme_id, num_parts, set_img_url, set_url).
  - `GET /lego/sets/{set_num}/parts/` — full inventory parts list with colors, quantities, spare flags. Essential if you ever want to compute parted-out value yourself from BrickLink prices.
  - `GET /lego/sets/{set_num}/minifigs/` — minifigs included (via 3rd-party clients; confirm against the live Swagger at the URL below).
  - `GET /lego/sets/{set_num}/alternates/` — MOCs built from this set.
  - `GET /lego/themes/` — theme dictionary to resolve `theme_id`.
- **Live docs / Swagger:** `https://rebrickable.com/api/v3/docs/` and `https://rebrickable.com/api/v3/swagger/`.
- **Data dumps:** `https://rebrickable.com/downloads/` — free CSVs of sets, parts, colors, inventories, themes, minifigs. **Use these to prime your database on first run**, then call the API only for deltas and user actions. This is the single biggest cost saver.
- **Important caveat:** Rebrickable does **not** publish price data. It is a catalog, not a marketplace. Pair it with Brickset/BrickLink for pricing.
- **Important caveat #2:** Rebrickable does **not** index sets by UPC/EAN barcode. Barcode → set_num requires Brickset or a UPC database.

### 1.2 Brickset — catalog + pricing + UPC/EAN

- **Base URL:** `https://brickset.com/api/v3.asmx/`
- **Format:** Technically an ASMX (.NET SOAP) endpoint, but v3 accepts and returns JSON. Methods are invoked as `GET` or `POST` to `v3.asmx/{method}` with parameters.
- **Auth:** Two-part.
  - `apiKey` — requested at `https://brickset.com/tools/webservices/requestkey/` (free, usually approved within a day).
  - `userHash` — obtained by calling `login` with your Brickset username/password. Required for any call that touches user collections; read-only catalog calls accept just `apiKey`.
- **Rate limits:** Not publicly published as a hard number; community reports it's comfortable for individual apps but not bulk scraping. Stay under a few calls per second and cache.
- **Key methods:**
  - `getSets` — catalog search. Parameters are passed as a JSON string in the `params` query arg, e.g. `params={"setNumber":"10294-1"}` or `params={"query":"5702014264335"}` (EAN). This is what you call with a scanned barcode.
  - `getSet` / `getSetByID` — single set by Brickset ID.
  - `getAdditionalImages` — all box/instruction images.
  - `getInstructions` — PDF links to official instructions.
  - `getReviews`, `getMinifigCollection`, `getCollectionTotals`, etc.
- **Returned set fields that matter for this project:**
  - `number`, `numberVariant`, `setID`, `name`, `year`, `theme`, `themeGroup`, `subtheme`, `pieces`, `minifigs`
  - `barcodes.EAN`, `barcodes.UPC` — **the UPC/EAN data for set lookup**
  - `LEGOCom.US.retailPrice`, `LEGOCom.UK.retailPrice`, etc. — MSRP
  - `collections.ownedBy`, `collections.wantedBy` — popularity signals
  - Recent additions include an "averageSellingPrice" / sealed resale metric — check the live docs for current field names, they evolve
- **Docs:** `https://brickset.com/article/52664/api-version-3-documentation`
- **Example call (Python):**
  ```python
  import requests, json
  params = {"setNumber": "10294-1"}
  r = requests.get(
      "https://brickset.com/api/v3.asmx/getSets",
      params={
          "apiKey": BRICKSET_KEY,
          "userHash": "",           # empty for read-only catalog calls
          "params":  json.dumps(params),
      },
  )
  sets = r.json()["sets"]
  ```

### 1.3 BrickLink — catalog + the definitive price guide

- **Base URL:** `https://api.bricklink.com/api/store/v1/`
- **Auth:** OAuth 1.0a with **four** permanent, static credentials issued once from your BrickLink account (Account → API Keys): `consumer_key`, `consumer_secret`, `token_value`, `token_secret`. No OAuth dance — treat them like static API keys and sign each request.
- **IP allowlisting:** BrickLink requires you to register the source IPs you'll call from. Awkward for dynamic/serverless deploys; a fixed NAT or a small proxy VM solves it.
- **Cost:** Free.
- **Rate limit:** Moderate, intended for apps and small store integrations; respect the rate headers and back off on 429. Community consensus is to stay well under ~5k/day per key.
- **Catalog endpoints:**
  - `GET /items/{type}/{no}` — where `{type}` is one of `SET`, `PART`, `MINIFIG`, `GEAR`, `BOOK`, `CATALOG`, etc. For a set: `GET /items/SET/10294-1`.
  - `GET /items/{type}/{no}/subsets` — inventory (parts & minifigs) for parted-out calculations.
- **Price Guide endpoints (the reason to integrate BrickLink):**
  - `GET /items/SET/{no}/price?new_or_used=N&guide_type=sold`
  - `GET /items/SET/{no}/price?new_or_used=U&guide_type=stock`
  - `guide_type=sold` returns last-six-months sold stats: `min_price`, `max_price`, `avg_price`, `qty_avg_price`, `unit_quantity`, `total_quantity`, plus `price_detail[]` per transaction.
  - `guide_type=stock` returns currently-listed inventory stats (what sellers are asking right now).
  - `new_or_used=N|U` — sealed or used.
  - Accepts `currency_code` for conversion from USD.
- **Example call (pseudocode, any OAuth1-capable HTTP client):**
  ```
  GET https://api.bricklink.com/api/store/v1/items/SET/10294-1/price?new_or_used=N&guide_type=sold
  Authorization: OAuth oauth_consumer_key="...",
                 oauth_token="...",
                 oauth_signature_method="HMAC-SHA1",
                 oauth_timestamp="...",
                 oauth_nonce="...",
                 oauth_version="1.0",
                 oauth_signature="..."
  ```
- **Ready-made clients:**
  - Python: `bricklink-py`, `FrogCosmonaut/bricklink_py`
  - C#: `BricklinkSharp`
  - PHP: `davesweb/bricklink-api`
  - Java: `e-amzallag/bricklinkapi`
  - PowerShell: `adbertram/Bricklink`
- **Note:** BrickLink is the canonical data source for **used** and **parted-out** values. It's weaker for sealed-set resale than Brickset/PriceCharting/BrickEconomy because sealed sets aren't what BrickLink primarily moves.

---

## 2. Pricing Layer (Sealed / New)

Sealed-set resale is the hardest data to get cleanly for free. Options, roughly best-to-worst:

### 2.1 Brickset — easiest free option

Brickset exposes current average selling prices on its set pages and the same data is available through the API per set. It's updated from a combination of eBay sold listings and Brickset user data. You already have Brickset in the stack from §1.2 for metadata and UPC, so this is effectively free pricing with one call.

**What to expect:** Recent retail prices (USD/GBP/EUR), current estimated value for sealed, and in many cases a historical trend field. Field names are under active development — read them from the JSON response rather than hard-coding.

### 2.2 BrickEconomy — best consumer site, API is paid/unclear

- **Site:** `https://www.brickeconomy.com/` — tracks 20,000+ sets across 25+ marketplaces, updated hourly. The UI shows current market value, forecast, CAGR, and history graphs. For a human looking up "what's my set worth", this is the best answer.
- **API:** Documented at `https://www.brickeconomy.com/api-reference`. Base URL `https://www.brickeconomy.com/api/v1/`. Endpoints for Sets, Minifigs, Themes, Pricing, Analysis. Auth is API-key-in-header with optional OAuth2.
- **Cost:** BrickEconomy Premium is a paid consumer product; their API tier pricing is not publicly posted and appears to gate commercial access. Treat as paid until you reach their support and get a free-tier answer.
- **Fallback:** People do scrape BrickEconomy (examples exist on GitHub, e.g. `JeremyEudy/Lego-Price-Scraper`, `CalDevC/LEGO-Investing-Portfolio-Manager`). That's fragile and against TOS — not recommended for anything you'll run long-term.

### 2.3 PriceCharting — best sealed data, paid API only

- **Site:** `https://www.pricecharting.com/category/lego-sets`
- PriceCharting ingests every eBay LEGO sale and assigns each transaction to a set, then publishes prices by grade: **Loose / CIB / New (sealed) / Graded**. For sealed market value this is the cleanest single source.
- **API:** Documented at `https://www.pricecharting.com/api-documentation`. JSON, token auth. **Requires a paid subscription** — the API is a premium feature. Prices are returned in integer pennies. If you later want the best-in-class sealed valuation and are willing to pay, this is where to go.
- **Free alternative using the same underlying eBay data:** eBay's Browse API (see below) gives you live and recent listings, but the "sold" dataset eBay exposes is limited to approved high-volume developers (Terapeak). This is why everyone builds on top of PriceCharting's summary instead.

### 2.4 eBay APIs — raw signal, free but limited

- **Browse API:** `https://developer.ebay.com/api-docs/buy/browse/overview.html` — free, OAuth2, high rate limits. Great for **currently listed** asking prices. Search by keyword (`"LEGO 10294 Titanic sealed new"`) and filter to Buy-It-Now. Won't give you sold history.
- **Marketplace Insights / Finding API:** Restricted to large integrators (Terapeak). Not realistic for a personal app.
- **Takeaway:** Use eBay Browse as a live "what's it listed at right now" signal and pair with Brickset's historical average. If you need real sold-price history and can't pay, your only realistic path is a 90-day in-house scrape of public eBay sold listings (eBay shows 90 days of sold data publicly) — which is allowed in limited personal research but becomes TOS-gray at scale.

---

## 3. Pricing Layer (Used / Parted-Out)

### 3.1 BrickLink Price Guide — the standard

Covered in §1.3. For used sets, `guide_type=sold&new_or_used=U` on `/items/SET/{no}/price` gives six-month sold stats. For parted-out value, walk the inventory (`/items/SET/{no}/subsets`) and fetch the price guide per part in its specific color, then sum. This is exactly what tools like Bricqer and BrickStore do, and it's the number most sellers quote.

**Performance tip:** Parted-out calculation for a big set means hundreds of part price-guide calls. Cache aggressively (24–72h is fine for slow-moving parts), and consider pre-loading common parts from Rebrickable's CSVs + the BrickLink catalog dumps.

### 3.2 BrickOwl — BrickLink alternative

- **Site & docs:** `https://www.brickowl.com/api_docs`
- Simpler REST API with standard API-key auth, offers a price guide and catalog. Smaller marketplace so thinner data on rare parts/sets, but easier to integrate than BrickLink's OAuth1 + IP-allowlist setup. Good as a cross-check source or a lighter alternative for prototypes.

---

## 4. Barcode / UPC Layer

Barcodes on a LEGO box will be one of **EAN-13** (global) or **UPC-A** (US). Both are printed on virtually every retail set.

### 4.1 Brickset — first try

Brickset stores the official EAN and UPC per set (§1.2). Calling `getSets` with `params={"query":"<scanned-barcode>"}` will match most in-print LEGO sets directly. This should be your first call for any scanned barcode — you get the set number *and* metadata in one shot, no second service needed.

**Gotcha:** Multi-pack polybags, regional variants, and older retired sets sometimes have barcodes Brickset doesn't carry. That's why you need a fallback.

### 4.2 UPCitemdb — free fallback

- **Endpoint:** `https://api.upcitemdb.com/prod/trial/lookup?upc=<code>`
- **Explorer (free) tier:** 100 requests per day, **no signup required**. Response includes product title, brand, description, images, and offers. For LEGO, the title usually includes the set number — strip it out with a regex and re-query Brickset/Rebrickable for structured data.
- **Paid tiers** scale to higher volumes and expose `search` endpoints.
- **Docs:** `https://devs.upcitemdb.com/`

### 4.3 Go-UPC, Barcode Lookup, UPCDatabase.org, Buycott — other fallbacks

All offer free test tiers and public REST APIs. Any one of these is a reasonable second fallback if UPCitemdb doesn't have the code. Their data on LEGO specifically is variable; pick one and stick with it.

### 4.4 EAN-Search

- **Endpoint:** `https://www.ean-search.org/ean-api-intro.html`
- Paid API with a small free trial. Useful only if you need European EANs that US-centric databases miss.

---

## 5. Image Recognition (Bonus — for Sets Without a Readable Barcode)

### 5.1 Brickognize

- **Site:** `https://brickognize.com/`
- **API base:** `https://api.brickognize.com/`
- **Docs (Swagger UI):** `https://api.brickognize.com/docs`
- **Auth:** None required for public endpoints (rate-limited by IP).
- **Endpoints:** Image upload returns best-match candidates across parts, sets, and minifigs. Extensively used for automatic sorting machines (Bricqer integrates it natively).
- **Good for:** Identifying a set from a blurry photo, identifying a minifig from a character photo, identifying a single part.
- **Not good for:** Replacing the barcode flow — image recognition is slower and less precise than a UPC scan when the box is intact.

### 5.2 RebrickNet

- **Site:** `https://rebrickable.com/rebricknet/`
- Part-detection AI trained on the Rebrickable catalog. Better for loose parts than for whole sets. Useful once you're inventorying opened sets or bulk bricks, not for scanning sealed boxes.

---

## 6. Comparison Table

| Service | Set lookup by number | UPC/EAN lookup | Sealed price | Used price | Parted-out | Auth | Free tier | Rate limit |
|---|---|---|---|---|---|---|---|---|
| **Rebrickable** | Yes (canonical) | No | No | No | Indirectly (via parts list) | `Authorization: key <k>` | Yes, unlimited-ish | Throttled; use CSV dumps for bulk |
| **Brickset** | Yes | **Yes** (EAN + UPC fields) | Yes (avg + retail) | Weak | No | API key + userHash | Yes, free after approval | Unpublished, polite use OK |
| **BrickLink** | Yes | No | Weak | **Yes (best)** | **Yes (best)** | OAuth1 (4 static tokens) + IP allowlist | Yes | Moderate, per-key |
| **BrickOwl** | Yes | No | Yes | Yes | Yes | API key | Yes | Moderate |
| **BrickEconomy** | Yes | No | **Yes (best UI)** | Yes | Limited | API key / OAuth2 | Unclear — treat as paid | Hard bans over limits |
| **PriceCharting** | Yes (by name/id) | No | **Yes (best data)** | Yes (CIB/Loose) | No | Token | No — paid | Generous once paid |
| **eBay Browse API** | Via keyword search | Via keyword search | Live asking only | Live asking only | No | OAuth2 | **Yes** | High (5k/day typical) |
| **UPCitemdb** | No | **Yes (free, no signup)** | No (retailer offers only) | No | No | None / key for paid | **Yes, 100/day** | 100/day on Explorer |
| **Go-UPC / Barcode Lookup** | No | Yes | No | No | No | API key | Yes (trial) | Varies |
| **Brickognize** | Via image | No | No | No | No | None | **Yes** | Rate-limited anonymously |

---

## 7. Recommended App Architecture (jay's chosen stack)

The target deployment is a **Mac mini** running the Python backend (web UI, CLI, DB, scheduled jobs) with an **iPhone / SwiftUI** client for in-hand photo-and-scan input. API keys and scheduled scraping stay server-side on the mini; the phone is a thin client that talks to the mini.

### 7.1 High-level diagram

```
  ┌──────────────────────────────────────┐
  │ iPhone — SwiftUI client              │
  │ · Camera / barcode scan (VisionKit)  │
  │ · Photo capture → upload             │
  │ · Inventory browse & edit UI         │
  └─────────────┬────────────────────────┘
                │  HTTPS (LAN or Tailscale)
                ▼
  ┌──────────────────────────────────────────────────────────┐
  │ Mac mini — always-on server                              │
  │                                                          │
  │  FastAPI (Python)         CLI (Typer/Click)              │
  │   └── /api/lookup         └── brickblade import, query   │
  │   └── /api/inventory                                     │
  │   └── /api/refresh                                       │
  │                                                          │
  │  SQLite (or Postgres)                                    │
  │   ├── catalog_*  (mirrored from Rebrickable CSVs)        │
  │   ├── inventory  (user-owned sets, timestamps)           │
  │   └── prices     (append-only price snapshots)           │
  │                                                          │
  │  launchd scheduled jobs                                  │
  │   ├── weekly: pull Rebrickable CSV dumps → refresh DB    │
  │   ├── nightly: refresh Brickset sealed price for owned   │
  │   └── nightly: refresh BrickLink used/parted-out         │
  └─────────────┬────────────────────────────────────────────┘
                │ outbound only
                ▼
  Rebrickable CSVs · Brickset API · BrickLink API
  UPCitemdb · Brickognize
```

### 7.2 Data layer — Rebrickable mirror on a schedule

- **Where:** SQLite is fine to start (single-file, backed up with Time Machine). Move to Postgres on the mini if the iPhone app ever grows multi-user or you want concurrent writes.
- **What to import:** From `https://rebrickable.com/downloads/` — `themes.csv`, `sets.csv`, `inventories.csv`, `inventory_sets.csv`, `inventory_parts.csv`, `inventory_minifigs.csv`, `minifigs.csv`, `colors.csv`, `parts.csv`, `part_categories.csv`, `part_relationships.csv`, `elements.csv`.
- **How often:** Rebrickable publishes new dumps roughly daily. Weekly is fine for a catalog that barely changes between dumps.
- **Scheduling on macOS:** prefer **launchd** (native, survives reboots, no third-party dependency) over cron. Example `~/Library/LaunchAgents/com.brickblade.refresh-catalog.plist` triggering a Python script weekly at 03:00.
- **Alternative in-process:** **APScheduler** inside the FastAPI app. Simpler one-binary deploy, but jobs stop when the app does — fine for a dev machine, less robust than launchd for a Mac mini that's running 24/7.
- **Import script pattern:** download into a temp dir, hash-diff against last run, `UPSERT` into the DB inside a single transaction so readers never see a half-loaded state. Keep the raw CSVs in a dated archive folder so you can diff or roll back.

### 7.3 Pricing layer — nightly refresh, on-demand top-up

- **Owned sets only.** Never price-refresh the entire Rebrickable catalog — that's a waste of API budget. Only refresh the rows in your `inventory` table.
- **Nightly scheduled job (launchd):**
  - For each owned set: one Brickset `getSets` call (sealed retail + recent avg).
  - For each owned set: one BrickLink Price Guide call for `used / sold`, and optionally one for `new / sold`.
  - Append a row to a `prices` table keyed by `(set_num, source, condition, fetched_at)`. Append-only means you get free historical trend data for your own collection over time — a nice long-term side effect.
  - Stagger calls (e.g. one request per 2 seconds) to stay friendly.
- **On-demand "hot lookup"** from the phone: the `/api/lookup` endpoint serves cached prices instantly; if the last snapshot is older than a configurable TTL, it fires a fresh fetch synchronously and returns the new value.
- **Parted-out calculation** (optional power feature): store `inventory_parts` already from the CSV mirror, cache BrickLink part prices in a separate `part_prices` table with a 7-day TTL, and compute the parted-out sum on demand. First run for a big set will be slow; subsequent runs are instant from cache.

### 7.4 Backend stack on the Mac mini

Concrete pick so you can start typing:

- **Runtime:** Python 3.12+ in a project-local `venv` (or `uv` for speed).
- **Web framework:** **FastAPI** — async, great type support, auto-generates OpenAPI (which makes the iOS client easier too, because you can generate a Swift client from the spec).
- **ORM / DB:** SQLAlchemy 2.x + SQLite for v1; swap in Postgres by changing the URL.
- **HTTP client:** `httpx` (async) for outbound API calls. `requests_oauthlib` for the BrickLink OAuth1 signing (or hand-sign with the `oauthlib` primitives if you want zero deps).
- **CLI:** **Typer** (or Click). Commands: `brickblade import`, `brickblade add 10294`, `brickblade refresh-prices`, `brickblade value-of --theme "Star Wars"`, etc. The CLI shares the DB and the API-client code with the web app — keep them in a single `brickblade` package.
- **Process manager:** launchd (`KeepAlive`, `RunAtLoad`) for the FastAPI process itself, so the mini reboots cleanly and the server comes back automatically.
- **Local HTTPS:** for the phone to talk to the mini securely without raw HTTP, the simplest options are (a) **Tailscale** — both devices join your tailnet and you get a stable `mac-mini.tail-scale.ts.net` hostname with TLS handled for you; or (b) a self-signed cert trusted on the phone via a mobileconfig profile. Tailscale is far less fiddly and also lets you hit the mini from outside your home network.

### 7.5 iPhone client — SwiftUI

Split by iOS version — `VisionKit.DataScannerViewController` (iOS 16+) is the modern path and dramatically simpler than rolling AVFoundation. Support matrix:

- **Barcode scanning:** `DataScannerViewController` with `.recognizedDataTypes = [.barcode(symbologies: [.ean13, .upca, .ean8, .qr])]`. Returns the decoded code to SwiftUI via a delegate.
- **Still photos for image recognition:** `PhotosPicker` or `UIImagePickerController` for capture → `URLSession` multipart upload to your mini's `/api/identify-image` endpoint, which proxies to Brickognize server-side. Proxying matters because it (a) keeps Brickognize rate-limit hits centralized, (b) lets you enrich results with your local catalog before returning.
- **On-device OCR fallback** for set numbers printed on the box: `VNRecognizeTextRequest` from Vision framework, run locally, send the regex-extracted number to `/api/lookup`. Works even without a data connection.
- **Offline behavior:** cache the most recent lookup results and the user's inventory list locally with SwiftData or Core Data so the app is useful on the go without a mini connection. Syncs back when the phone reaches the tailnet.
- **Distribution:** for personal/family use, a free Apple Developer account + Xcode direct install to your devices is sufficient (app must be re-signed every 7 days, or yearly with a paid account). If you want it to "just work" on multiple family phones indefinitely, the \$99/yr paid account + TestFlight is the path.

### 7.6 Endpoints the mini should expose to the phone

Minimum viable set:

```
POST /api/lookup            { barcode?: str, set_num?: str, photo_id?: uuid }
                            → { set_num, name, year, theme, pieces,
                                image_url, sealed_price, used_price,
                                parted_out, confidence, sources: [...] }

POST /api/identify-image    multipart image → { candidates: [set_num, score] }

POST /api/inventory         { set_num, qty, condition, notes } → 201
GET  /api/inventory         → list
DELETE /api/inventory/{id}

POST /api/refresh-now       (admin) force immediate price refresh for owned sets
GET  /api/health
```

Authenticate with a long-lived bearer token stored in iOS Keychain. Not fort-knox, but appropriate for a household app on a private tailnet.

### 7.7 Input flow, end-to-end

1. **Phone** — user points camera at box, `DataScannerViewController` returns `5702014264335`.
2. **Phone → mini** — `POST /api/lookup {"barcode": "5702014264335"}`.
3. **Mini** — checks local cache; miss → Brickset `getSets` with `query=5702014264335` → resolves to `10294-1`. Hydrates metadata from the local Rebrickable mirror (no API call). Fetches/returns latest cached Brickset sealed price + BrickLink used price; triggers a background refresh if TTL exceeded.
4. **Phone** — displays set, current values, "Add to inventory" button.
5. **Later, nightly** — launchd job on the mini refreshes prices for every owned set; next time the phone opens, values are current without any on-device work.

---

## 8. Implementation Notes & Gotchas

- **Set number format.** Everyone uses `{number}-{variant}` internally (e.g. `10294-1`, `75192-1`). The box usually just prints `10294`. Always append `-1` for lookups unless you know there's a variant. Rebrickable and BrickLink both require the variant suffix; Brickset will accept either.
- **Barcode on the box ≠ unique set.** LEGO reuses the same set number across regional variants with slightly different box art, but usually the **EAN/UPC differs per region**. Brickset stores one primary barcode per set — if a barcode scan misses, fall back to the set number printed alongside (usually top-right of the box).
- **Currency.** Brickset returns multi-currency retail/price fields. BrickLink Price Guide returns USD by default; pass `currency_code` for conversion. BrickEconomy is USD. Store prices with a currency field, not a single column.
- **Caching strategy.** Catalog data almost never changes — cache indefinitely, invalidate manually when Rebrickable publishes a new dump. Price data changes daily — 24–72h TTLs are fine for anything except day-traders.
- **Rate-limit etiquette.** All of these services are volunteer-run or small-team. Respect rate headers, back off on 429, and don't hammer. BrickLink IP-allowlisting means you can't trivially distribute load across a fleet.
- **Legal.** Rebrickable CSVs are licensed for personal/non-commercial use by default; commercial use requires permission. BrickLink TOS allows personal apps but bans redistribution of their Price Guide data as a dataset. PriceCharting and BrickEconomy are strictly paid for commercial use.
- **OCR on boxes.** If you want to extract the set number from a box photo without a barcode, Tesseract works but struggles with stylized LEGO typography; the modern approach is a vision model (Claude/GPT-4V) with a prompt like "return only the set number from this LEGO box image."

---

## 9. Example: Minimal Python Lookup Flow

```python
import os, json, requests
from requests_oauthlib import OAuth1

REBRICKABLE_KEY = os.environ["REBRICKABLE_KEY"]
BRICKSET_KEY    = os.environ["BRICKSET_KEY"]
BL = OAuth1(
    os.environ["BL_CONSUMER_KEY"],
    os.environ["BL_CONSUMER_SECRET"],
    os.environ["BL_TOKEN"],
    os.environ["BL_TOKEN_SECRET"],
)

def normalize_set_num(s: str) -> str:
    return s if "-" in s else f"{s}-1"

def lookup_by_barcode(barcode: str) -> str | None:
    """Barcode → set_num via Brickset, with UPCitemdb fallback."""
    r = requests.get(
        "https://brickset.com/api/v3.asmx/getSets",
        params={
            "apiKey":   BRICKSET_KEY,
            "userHash": "",
            "params":   json.dumps({"query": barcode}),
        },
    ).json()
    if r.get("sets"):
        return normalize_set_num(r["sets"][0]["number"])
    # fallback
    alt = requests.get(
        "https://api.upcitemdb.com/prod/trial/lookup",
        params={"upc": barcode},
    ).json()
    for item in alt.get("items", []):
        # titles usually contain the set number, e.g. "LEGO 10294 Titanic"
        import re
        m = re.search(r"\b(\d{4,6})\b", item.get("title", ""))
        if m:
            return normalize_set_num(m.group(1))
    return None

def get_metadata(set_num: str) -> dict:
    r = requests.get(
        f"https://rebrickable.com/api/v3/lego/sets/{set_num}/",
        headers={"Authorization": f"key {REBRICKABLE_KEY}"},
    )
    r.raise_for_status()
    return r.json()

def get_sealed_price(set_num: str) -> dict:
    r = requests.get(
        "https://brickset.com/api/v3.asmx/getSets",
        params={
            "apiKey":   BRICKSET_KEY,
            "userHash": "",
            "params":   json.dumps({"setNumber": set_num}),
        },
    ).json()
    return r["sets"][0] if r.get("sets") else {}

def get_used_price(set_num: str) -> dict:
    r = requests.get(
        f"https://api.bricklink.com/api/store/v1/items/SET/{set_num}/price",
        params={"new_or_used": "U", "guide_type": "sold", "currency_code": "USD"},
        auth=BL,
    )
    r.raise_for_status()
    return r.json()["data"]

# Example
set_num = lookup_by_barcode("5702014264335") or normalize_set_num("10294")
print(get_metadata(set_num)["name"])
print(get_sealed_price(set_num).get("LEGOCom", {}).get("US", {}).get("retailPrice"))
print(get_used_price(set_num))  # avg_price, min_price, max_price, unit_quantity, ...
```

---

## 10. Decision Summary

**Chosen path (this project):** the **free 3-API stack + local Rebrickable mirror, deployed on a Mac mini with a SwiftUI iPhone client**. Concretely:

- **Integrations:** Rebrickable (catalog, both via API and via CSV dumps mirrored locally), Brickset (UPC/EAN lookup + sealed price), BrickLink (used & parted-out pricing). UPCitemdb as a barcode fallback, Brickognize for image-based ID.
- **Backend:** Python (FastAPI + Typer CLI) on the Mac mini, SQLite to start, launchd-driven scheduled jobs to (a) pull fresh Rebrickable CSVs weekly and (b) refresh prices nightly for owned sets. Append-only price-history table gives you a free BrickEconomy-style trend graph of your own collection over time.
- **Client:** SwiftUI app on iPhone using `DataScannerViewController` for barcodes, `PhotosPicker`/Vision for images, talking over HTTPS to the mini (Tailscale recommended for the transport).
- **Upgrade paths if you outgrow free:** swap Brickset sealed-price for **PriceCharting** (paid, best-in-class eBay-sold data). Swap SQLite for Postgres on the same mini. Layer **eBay Browse API** in as a live asking-price signal without changing anything else.

Alternative paths intentionally not taken here, for the record:

- *Single-service simplicity, paid:* PriceCharting + Brickset. Fewer moving parts, no BrickLink OAuth1, but costs ~\$200/yr and you lose parted-out calculations.
- *Fully cloud-native:* same app, deployed to a small VPS or Fly.io app instead of a Mac mini. Same code, different host. The Mac mini wins here because you already own it, it's always on, and iOS→mini over Tailscale is effectively free.

---

## Sources

- [Rebrickable API Documentation](https://rebrickable.com/api/)
- [Rebrickable API v3 Swagger](https://rebrickable.com/api/v3/swagger/)
- [Rebrickable — LEGO Database Help](https://rebrickable.com/help/lego-database/)
- [Rebrickable — Free Database Downloads](https://rebrickable.com/downloads/)
- [pyrebrickable README — Rebrickable endpoint reference](https://github.com/rienafairefr/pyrebrickable/blob/master/rebrickable/api_README.md)
- [Brickset API v3 Documentation](https://brickset.com/article/52664/api-version-3-documentation)
- [Brickset Web Services overview](https://brickset.com/article/52666/brickset-web-services)
- [Brickset — New API version announcement](https://brickset.com/article/49510/new-version-of-brickset-api-now-available)
- [Brickset API TypeScript client (brakbricks/brickset-api)](https://github.com/brakbricks/brickset-api)
- [Brickset forum — API getSets params examples](https://forum.brickset.com/discussion/35705/api-getsets-params-example)
- [BrickLink API welcome page](https://www.bricklink.com/v2/api/welcome.page)
- [BrickLink API Authorization reference (OAuth1)](http://apidev.bricklink.com/redmine/projects/bricklink-api/wiki/Authorization)
- [BrickLink Price Guide help](https://www.bricklink.com/help.asp?helpID=31)
- [bricklink_py — Python wrapper](https://github.com/FrogCosmonaut/bricklink_py)
- [BricklinkSharp — C# client](https://github.com/gebirgslok/BricklinkSharp)
- [davesweb/bricklink-api — PHP SDK](https://github.com/davesweb/bricklink-api)
- [adbertram/Bricklink — PowerShell](https://github.com/adbertram/Bricklink)
- [Connecting N8N to BrickLink API via OAuth1](https://www.technetexperts.com/n8n-bricklink-oauth1-guide/)
- [BrickOwl API Documentation](https://www.brickowl.com/api_docs)
- [BrickEconomy](https://www.brickeconomy.com/)
- [BrickEconomy API Reference](https://www.brickeconomy.com/api-reference)
- [BrickEconomy FAQ](https://www.brickeconomy.com/faqs)
- [BrickEconomy Premium](https://www.brickeconomy.com/premium)
- [PriceCharting LEGO Set Price Guide](https://www.pricecharting.com/category/lego-sets)
- [PriceCharting API Documentation](https://www.pricecharting.com/api-documentation)
- [PriceCharting methodology](https://www.pricecharting.com/page/methodology)
- [eBay Browse API overview](https://developer.ebay.com/api-docs/buy/browse/overview.html)
- [UPCitemdb Developer API](https://devs.upcitemdb.com/)
- [UPCitemdb — LEGO UPC coverage](https://www.upcitemdb.com/info-lego)
- [Go-UPC API plans](https://go-upc.com/plans/api)
- [Barcode Lookup API](https://www.barcodelookup.com/api)
- [UPCDatabase.org](https://upcdatabase.org/api)
- [Buycott — free UPC API](https://www.buycott.com/)
- [EAN-Search API intro](https://www.ean-search.org/ean-api-intro.html)
- [Brickognize](https://brickognize.com/)
- [Brickognize API (Swagger UI)](https://api.brickognize.com/docs)
- [RebrickNet — LEGO part detector](https://rebrickable.com/rebricknet/)
- [Brickset — Barcode scanning on mobile site](https://brickset.com/article/679/barcode-scanning-now-supported-on-the-mobile-site)
- [Bricqer — Using the API](https://www.bricqer.com/guides/using-the-api)
- [Bricqer — Pricing formulas guide](https://www.bricqer.com/guides/pricing-formulas)
