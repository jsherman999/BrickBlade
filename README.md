# BrickBlade

A smart inventory app for a large LEGO collection. Scan or type a set, get catalog metadata and current sealed / used / parted-out market values, and track value changes over time — all from free public APIs.

## What's here

- **`lego-inventory-apis-research.md`** — developer-focused research report covering the LEGO APIs, pricing sources, barcode lookup services, and image-recognition tools that feed the app. Includes a comparison table, architecture diagrams, example Python, and a decision summary.

## Chosen stack (summary)

Full detail lives in `lego-inventory-apis-research.md`; the short version:

- **Catalog** — [Rebrickable](https://rebrickable.com/api/), mirrored locally from their daily CSV dumps.
- **Sealed price + UPC/EAN lookup** — [Brickset API v3](https://brickset.com/article/52664/api-version-3-documentation).
- **Used & parted-out price** — [BrickLink Price Guide API](https://www.bricklink.com/v2/api/welcome.page).
- **Barcode fallback** — [UPCitemdb](https://devs.upcitemdb.com/) free tier (100/day, no signup).
- **Image recognition** — [Brickognize](https://brickognize.com/) for box-photo identification.

All free or freemium. No single service covers everything; the app combines them.

## Target deployment

- **Backend** — Python (FastAPI + Typer CLI) on a Mac mini, SQLite to start, launchd-scheduled jobs to refresh the catalog weekly and owned-set prices nightly. An append-only `prices` table gives you free historical value tracking for your own collection.
- **Client** — SwiftUI app on iPhone: `DataScannerViewController` for barcode scanning, `PhotosPicker` / Vision for image capture, HTTPS over Tailscale to the mini.

```
iPhone (SwiftUI) ──► Mac mini (FastAPI + SQLite + launchd jobs) ──► Rebrickable / Brickset / BrickLink / UPCitemdb / Brickognize
```

## Status

Research phase. Implementation hasn't started yet.

## License

Personal project. Rebrickable CSV data is used under their terms (personal / non-commercial).
