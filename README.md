# Smash Data Analytics (smashDA)

**Smash Data Analytics** is an end-to-end analytics engine that ingests raw tournament data from start.gg, cleans and normalizes inconsistent player records, computes performance metrics, and serves precomputed results via a custom API to a public-facing visualization website.

🔗 **Live site:** [https://smash.watch](https://smash.watch)  
🎨 **Frontend:** https://github.com/ozdotdotdot/smash-frontend  
📊 **Example API endpoint:**  
`/precomputed?state=GA&months_back=3`

**Tech Stack:**  
Python · SQLite · GraphQL · REST APIs · Data Processing · Linux · Next.js (frontend)

---

## TL;DR
I built a full analytics pipeline to answer a simple question:

> _How do players actually compare within a local competitive Smash scene?_

What started as a local Georgia dataset evolved into a scalable system that handles messy real-world data, infers missing information, computes meaningful metrics, and serves results to a production website.

---

## The Problem
Competitive Smash scenes lack objective, data-driven ways to evaluate player performance within a region. Existing stats often rely on raw win/loss counts, incomplete player profiles, or manually maintained rankings that don’t account for:

- Opponent strength
- Event size and context
- Incomplete or missing location data
- Time-bounded performance trends


I wanted a system that could answer **“How do I stack up locally?”** using actual tournament results.

---

## Constraints & Challenges
This project dealt with real-world limitations rather than clean, curated datasets:

- start.gg data is **incomplete and inconsistent**
- Many players do **not list a home state**
- Tournament participation varies widely by player
- Large-scale data ingestion required long-running batch jobs
- Hosting needed to remain **low-cost and self-managed**

Design decisions were made to prioritize correctness, scalability, and cost efficiency.

---

## Technical Approach
### 1. Data Ingestion

- Tournament data is pulled from **start.gg’s GraphQL API**
- Queries are parameterized by state, game, and time window
- Automated scripts handle large batch ingestions (some runs exceeded **7.5 hours** for national datasets)

Raw API responses include player IDs, match results, event metadata, and timestamps.

---

### 2. Data Processing & Cleaning

Raw data is not immediately usable. Processing is handled in Python using DataFrames to:

- Normalize missing or inconsistent fields
- Aggregate match-level data into player-level records
- Remove invalid or misleading entries
- Prepare datasets for metric computation

This step required iterative inspection using both Python and spreadsheet tools to validate assumptions and outputs.

---

### 3. Player Classification Logic

Player location data is often missing or unreliable.

To address this, I implemented a **participation-based heuristic**:

- A player is classified as belonging to a state if **>60% of their tournaments** occur in that state

This approach reduces reliance on self-reported metadata and improves accuracy as the dataset grows.

---

### 4. Metric Computation

Once cleaned and classified, the system computes several performance metrics:

- **Weighted Win Rate**  
    Adjusts win rate based on opponent strength
- **Opponent Strength**  
    Derived from historical win rates and standings
- **Event Context Metrics**  
    Average and maximum event size, large-event participation
- **Time-Window Filtering**  
    Metrics can be computed across rolling time ranges (e.g. last 3 months)

All metrics are precomputed to ensure fast downstream queries.

---

### 5. Storage Layer

Processed results are stored in a centralized **SQLite database (`smash.db`)**, which serves as the single source of truth for the system.

SQLite was chosen for its simplicity, reliability, and suitability for analytical workloads without requiring managed cloud infrastructure.

---

### 6. API Layer

A custom REST API exposes precomputed results to client applications.

Example endpoint:

`/precomputed?state=GA&months_back=3&limit=0`

This endpoint returns weighted win rates and opponent strength averages for all qualifying players in a given region and time window.

The API cleanly separates:

- Data ingestion and computation
- Data access and visualization

---

## Frontend Integration

This repository powers the backend and analytics engine only.

The public-facing website is implemented separately using **Next.js and TypeScript** and consumes the API endpoints exposed by smashDA.

Frontend repository:  
👉 https://github.com/ozdotdotdot/smash-frontend

---

## Results & Impact
- Enabled objective, data-driven player comparisons at the state level
- Scaled from a local (Georgia) dataset to national tournament data
- Reduced query latency via precomputation and API design
- Built a reusable analytics pipeline applicable to other regions and timeframes

---

## What I Learned
This project reinforced several core engineering lessons:

- Real-world data is messy and requires defensive design
- Inference logic often outperforms missing metadata
- Separating compute from read paths improves performance
- Cost-aware infrastructure choices matter at small scale
- End-to-end ownership exposes tradeoffs that siloed roles often miss

---

## Related Links
- 🌐 Live Site: [https://smash.watch](https://smash.watch)
- 🎨 Frontend Repo: https://github.com/ozdotdotdot/smash-frontend
- 📄 Technical Deep Dive & Getting Started: `docs/README.md`