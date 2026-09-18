# Mermaid recipes and failure modes

Skeletons to start from, and the layout failures that keep recurring. Read the failures section before debugging a diagram by trial and error. Most problems are one of these seven.

Every skeleton below uses real subjects from this repo. Copy the shape, then re-check the content against the code. See `padyar-vocabulary.md` for where each subsystem lives.

---

## Recurring failures

### 1. `direction` inside a subgraph is ignored when edges cross the subgraph

The most common cause of an unreadable diagram. `direction TB` inside a subgraph is silently dropped as soon as an edge connects a node inside it to a node outside it. The whole graph collapses into one row.

```
flowchart LR
    subgraph A["local tiers"]
        direction TB          %% ignored, T1 has an edge leaving the subgraph
        T1["Tier 1 retrieval"]
    end
    T1 --> AI
```

**Fix:** drop the subgraph boxes and let the edges create the columns. Group by colour instead.

```
flowchart LR
    T1["Tier 1 · local retrieval"] --> G1
    G1{{"score >= 0.70?"}} --> OUT["serve the matched entry"]

    classDef local   fill:#e8eefc,stroke:#4a6fa5,color:#000
    classDef outcome fill:#e6f4ea,stroke:#5a9e6f,color:#000
    class T1 local
    class OUT outcome
```

Keep subgraphs only when nothing crosses them, or when you accept the default direction.

### 2. Fan-in and fan-out spaghetti

`A & B & C & D --> TARGET` draws four crossing lines. If the nodes already sit in a subgraph, draw **one** edge from the subgraph:

```
LOCAL -->|"no tier was confident"| AI      %% not: T0 & T1 & T15 --> AI
```

Same claim, one line, no crossings.

### 3. `quadrantChart` label clipping and collisions

Labels render to the **right** of their point, so anything past `x` of about 0.8 is clipped at the frame. Quadrant titles sit near the top centre of each quadrant, so points at `y` between about 0.44 and 0.53 land on the lower titles.

Keep `x` at 0.8 or below, avoid `y` in 0.44 to 0.53, and space points at least 0.04 apart on `y` when they share an `x` band. Axis labels and point names are unquoted in the documented syntax, so avoid commas and colons inside names.

### 4. Unquoted punctuation breaks the parser

Parentheses, slashes and `#` inside a label need quotes: `A["reindex (background)"]`, not `A[reindex (background)]`. Quoting every label is the safe habit. `<br/>` works inside quoted labels for line breaks and `<b>` works for emphasis.

### 5. Dark mode makes styled nodes unreadable

A `classDef` that sets `fill` without `color` inherits the theme's text colour, which is dark in light mode and **light** in dark mode. A pale fill with no explicit `color` renders light on light and vanishes. That is exactly the case for the pale fills used throughout these recipes. Always pair them: `classDef x fill:#e8eefc,stroke:#4a6fa5,color:#000`.

### 6. `sequenceDiagram` notes are not flowchart labels

`Note over A,B:` takes plain text to end of line. No quoting, and `;` terminates the statement, so `Note over CHAT,PG: ids only;<br/>facts re-read` is a parse error. Keep notes short and unpunctuated. Use `<br/>` only inside quoted **flowchart** labels.

### 7. Deep `LR` graphs need horizontal scrolling

GitHub renders into a narrow column. More than about 5 ranks in `LR` forces the reader to scroll sideways. Prefer `TB` for deep graphs and keep `LR` for wide but shallow ones. Three ranks is ideal, which is the symptom, cause, outcome shape.

---

## Skeletons

### The tiered answer pipeline (the canonical one)

`CLAUDE.md` holds the authoritative ASCII version. This is the same thing in Mermaid, trimmed to the gates. Check every threshold against `app/config.py` before you ship it, and do not add a tier name that is not a real `source` string in `app/routers/chat.py`.

```mermaid
flowchart TB
    Q["visitor query"] --> PICK{{"a number, an ordinal,<br/>or an offered title?"}}
    PICK -->|"yes · local_pick · zero AI calls"| SERVE["serve that record,<br/>read from the database"]:::local
    PICK -->|no| T0{{"near-exact hit in the<br/>curated questions index?"}}
    T0 -->|"yes · Tier 0"| SERVE
    T0 -->|no| T1["Tier 1 · normalize + synonyms<br/>BM25 + model2vec + reranker"]:::local
    T1 --> G1{{"score >= TRUSTED_MATCH_THRESHOLD (0.70)?"}}
    G1 -->|"yes · Tier 1"| SERVE
    G1 -->|no| T15["Tier 1.5 · trained intent head<br/>app/services/intent.py"]:::local
    T15 --> G2{{"p >= INTENT_TRUST_THRESHOLD (0.6)?"}}
    G2 -->|"yes · Tier 1.5"| SERVE
    G2 -->|no| T2["Tier 2 · selection<br/>model sees ANSWER_TOPK=8 records<br/>and returns record IDS only"]:::ai
    T2 --> REND["our renderer writes every fact<br/>back out of the database"]:::local
    T2 -.->|"AI off or errored"| FB{{"score >= LOCAL_FALLBACK_THRESHOLD (0.45)?"}}
    FB -->|"yes · degraded"| SERVE
    FB -->|no| ASK["ask the visitor to rephrase,<br/>never an unrelated video"]:::fallback

    classDef local    fill:#e8eefc,stroke:#4a6fa5,color:#000
    classDef ai       fill:#fff4e5,stroke:#c98a3a,color:#000
    classDef fallback fill:#fde8e8,stroke:#c86a6a,color:#000
```

Caption to write under it: blue is local and costs nothing, orange is the one paid call, red is the refusal. The model in Tier 2 picks ids, it never writes a fact.

Note the deliberate exception to failure 2 below. Five "yes" edges land on one `SERVE` node, which is fan-in. The alternative, five separate terminal boxes, repeats the same box five times and reads worse. That is why every one of those edges carries the tier that took it: the label is what keeps the fan-in traceable. Rendered and checked, the lines stay followable.

### Decision and its contingencies (research doc, Decision section)

The navigational diagram at the top of `docs/features/<slug>/RESEARCH.md`. Keep it shallow: the pick, the gates it hangs on, and where each failed gate lands. Two chained gates is the practical ceiling. A third makes the reader trace a path instead of seeing one.

Gates are hexagons. Every gate edge is labelled with the answer that takes it, because an unlabelled branch out of a gate is unreadable. Name the fallback instead of pointing at "the alternative". A reader who only looks at this diagram should learn what ships if the gate fails.

```mermaid
flowchart TB
    D(["recommend · move companies out of the dataset table"]):::chosen
    D --> G1{{"gate 1 · do the existing<br/>record ids survive the move?"}}
    G1 -->|no| F1["fall back to a view over dataset<br/>keeps ids, costs a join per query"]:::fallback
    G1 -->|yes| G2{{"gate 2 · does retrieval keep<br/>recall@8 at 0.95 or above?"}}
    G2 -->|no| F2["abandon the move, index companies<br/>as a second corpus instead"]:::fallback
    G2 -->|yes| S["ship the table + migration 0013"]:::chosen

    classDef chosen   fill:#e6f4ea,stroke:#5a9e6f,color:#000
    classDef fallback fill:#fde8e8,stroke:#c86a6a,color:#000
```

When the recommendation is a policy split rather than a gated bet, the same three ranks carry it. The pick, the axis it splits on, and what each branch gets:

```mermaid
flowchart TB
    D(["recommend · one refusal rule for every tier"]):::chosen
    D --> AX{{"what did retrieval return?"}}
    AX -->|"score >= 0.70"| M["serve the entry"]:::chosen
    AX -->|"0.45 to 0.70, AI reachable"| A["hand it to the selection tier"]:::chosen
    AX -->|"below 0.45, AI unreachable"| SC["ask to rephrase, never guess"]:::forced

    classDef chosen fill:#e6f4ea,stroke:#5a9e6f,color:#000
    classDef forced fill:#fff4e5,stroke:#c98a3a,color:#000
```

### Subsystem architecture (spec flow section, ARCHITECTURE.md)

Layers as subgraphs, one edge per relationship, the protocol or the guarantee on the edge label. Dashed means proposed.

```mermaid
flowchart TB
    subgraph BROWSER["kiosk browser"]
        UI["static/chat/core.js"]
    end

    UI -->|"DF01 · POST /chat + HMAC token"| API["app/routers/chat.py"]
    API -->|"DF02 · origin allowlist + rate limit"| SEC["app/auth/security.py"]
    API -->|"DF03 · top-8 candidates"| SEARCH["app/services/search.py"]
    API -->|"DF04 · ids only"| ANS["app/services/answer.py"]
    ANS ==>|"DF05 · every fact re-read here"| PG[("PostgreSQL 16")]
    ANS -.->|"DF06 · proposed cache"| CACHE["answer cache"]

    classDef proposed stroke-dasharray: 5 5,color:#000
    class CACHE proposed
```

### Summary and detail pair (any diagram past about 15 nodes)

The summary carries the shape, the detail is for whoever needs it. Collapsed nodes list their contents, and the mapping table is what stops the summary from being a lie by omission. Summary flow IDs take an `S` prefix so the two sets never collide.

```mermaid
flowchart LR
    K["kiosk browser<br/>themes/ · core.js"] -->|"SDF01 · POST /chat"| APP["FastAPI app<br/>routers · services · auth"]
    APP -->|"SDF02 · db/queries.py"| DATA[("data<br/>PostgreSQL 16 · media/ · backups/")]

    classDef collapsed fill:#eef1f5,stroke:#8a93a0,color:#000
    class APP,DATA collapsed
```

| Summary node | Collapses                                                    | Detail flows |
| ------------ | ------------------------------------------------------------ | ------------ |
| `APP`        | `routers/chat.py`, `services/search.py`, `services/answer.py`, `auth/security.py` | DF01 to DF06 |
| `DATA`       | PostgreSQL, uploaded media, backup dumps                     | DF07 to DF09 |

The detail diagram is omitted here for space, so those ID ranges stand in for it. In a real pair, every ID in that column exists in the detail diagram beside it. A range pointing at nothing is exactly the omission the table is supposed to prevent.

Grey is the collapsed-node convention. It reads as "there is more behind this" rather than as a category.

### Ordering across participants (a protocol, a retry, a failover)

```mermaid
sequenceDiagram
    participant C as kiosk browser
    participant R as routers/chat.py
    participant W as services/ai/wrapper.py
    participant E as services/ai/engine.py
    participant P as provider adapter

    C->>R: POST /chat
    R->>W: padyar_ai.generate(task=chat)
    W->>E: load route in priority order
    E->>P: adapter.invoke on target 1
    P-->>E: 429 rate limit
    Note over E,P: rate limit is retryable AND failover eligible
    E->>P: retry target 1 with backoff
    P-->>E: 429 again
    E->>P: fail over to target 2
    P-->>E: 200
    E-->>W: AIResponse
    W-->>R: record ids only
    R-->>C: rendered answer, facts read from the database
```

### Lifecycle (a status column)

Real example: `company_leads.status` in `app/services/leads.py`. Three statuses, not six, because the review outcome is a different axis and lives on `dataset_edits.status`.

```mermaid
stateDiagram-v2
    [*] --> unverified: visitor captures the lead, OTP sent
    unverified --> verified: contact reads the code back
    verified --> completed: contact submits the form
    verified --> [*]: admin releases the lead
    completed --> [*]
```

`verified` is a normal resting state, not an error. Plenty of contacts never open the link. Say that in the caption, or the diagram implies a stuck state.

### Schema change (a spec that adds or reshapes tables)

```mermaid
erDiagram
    dataset   ||--o{ questions      : "answers"
    dataset   ||--o{ chat_logs      : "entry_id"
    companies ||--o{ company_leads  : "one live lead"
    company_leads ||--o{ dataset_edits : "review queue"
```

Keep it to keys and relationships. A column list gets long fast and a table reads better for that.

### Ranking candidates (research doc, alternatives)

```mermaid
quadrantChart
    title Retrieval options for the local tier
    x-axis Low effort --> High effort
    y-axis Lower value --> Higher value
    quadrant-1 Plan a slice
    quadrant-2 Do first
    quadrant-3 Cheap but not urgent
    quadrant-4 Defer
    BM25 only: [0.2, 0.55]
    BM25 plus local embeddings: [0.45, 0.8]
    Hosted vector database: [0.75, 0.62]
```

### Task dependency graph (a plan under docs/superpowers/plans/)

Show what stacks and what lands on its own. Label the edge with **why** it blocks.

```mermaid
flowchart LR
    T1["01 · migration + init_db mirror"] --> T2["02 · queries + service"]
    T1 --> T3["03 · admin API"]
    T2 ==>|"no endpoint before the gate exists"| T4["04 · router + auth"]
    T5["05 · docs + INDEX row"]

    classDef independent stroke-dasharray: 4 4,color:#000
    class T5 independent
```

A schema change always starts the chain here, because a migration and its `init_db()` mirror have to land before anything reads the column.

### Deploy and rollback

Six steps, and the order is the safety. See `deploy/padyar-deploy.sh`.

```mermaid
flowchart TB
    S1["1 · backup<br/>dump before anything changes"] --> S2["2 · checkout<br/>old process still serving"]
    S2 --> S3["3 · deps · pip install"]
    S3 --> S4["4 · migrate<br/>apply_migrations.py, one txn per file"]
    S4 --> S5["5 · restart<br/>new code goes live"]
    S5 --> S6{{"6 · health · 12 tries x 5s"}}
    S6 -->|green| DONE["deployed"]:::chosen
    S6 -->|red| RB["reset to the old sha + restart<br/>CODE rollback only"]:::fallback
    S3 -.->|failure| AB["abort, old version fully intact"]:::fallback
    S4 -.->|failure| AB

    classDef chosen   fill:#e6f4ea,stroke:#5a9e6f,color:#000
    classDef fallback fill:#fde8e8,stroke:#c86a6a,color:#000
```

Caption it: step 6 rolls back **code**, not data. Restoring the step-1 backup is a manual, confirmed action from Infrastructure -> Backups.

### Symptom, cause, outcome (a user-facing framing)

Three ranks, colour-coded, no subgraphs (see failure 1).

```mermaid
flowchart LR
    S1["visitor asks about a company<br/>and gets the first of 169 rows"] --> R1
    R1(["selection tier returns ids,<br/>renderer offers a numbered list"]) --> O1["visitor picks a number<br/>and gets that booth clip"]

    classDef symptom fill:#fde8e8,stroke:#c86a6a,color:#000
    classDef slice   fill:#e8eefc,stroke:#4a6fa5,color:#000
    classDef outcome fill:#e6f4ea,stroke:#5a9e6f,color:#000
    class S1 symptom
    class R1 slice
    class O1 outcome
```
