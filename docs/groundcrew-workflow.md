# Groundcrew — Autonomous Execution

The autonomous-execution phase of the [SDLC workflow](./sdlc-workflow.md). Groundcrew
polls a ticket system, runs an agent per ticket inside an isolated sandbox, opens a PR,
and tears the worktree down once the ticket is marked done.

```mermaid
flowchart TD
    %% ============ GROUNDCREW ============
    subgraph GC["🤖 Groundcrew (Autonomous Execution)"]
        direction TB
        G0[Get list of tickets] --> P1[Pull ticket]
        P1 --> G1[Create Worktree and Workspace]
        G1 --> GMIP[Mark In Progress]
        G1 --> TMUX
        subgraph TMUX["🖥️ tmux"]
            direction TB
            subgraph SAFEHOUSE["🏠 Agent Safehouse+Clearance"]
                direction TB
                G2[Setup worktree] --> G3[Launch Agent]
                G3 --> G4[Autonomous Prompt]
                G4 --> G5[Implement]
                G5 --> G6[Test / Lint]
                G6 --> G7[Review]
                G7 -- Fix --> G5
                G7 --> G8[Create PR]
                G8 --> G9[Babysit PR]
            end
        end
        subgraph CLEAN["🧹 Cleaner (runs every poll tick)"]
            direction TB
            GCT0["Find tickets now<br/>marked done"] --> GCT["Tear down Worktree<br/>close tmux · remove worktree"]
        end

        %% ---- Ticket system integration (plugin layer) ----
        subgraph BOARD["🎫 Board — ticket abstraction"]
            direction TB
            BAPI["API touchpoints<br/>verify() · fetch()<br/>resolveOne() · markInProgress()"]
            subgraph REG["🔌 Adapter registry (plugin system)"]
                direction LR
                LIN["Linear adapter"]
                SH["Shell adapter"]
            end
            BAPI --> LIN
            BAPI --> SH
        end

        G0 -. "fetch() · status = todo" .-> BAPI
        P1 -. "resolveOne()" .-> BAPI
        GMIP -. "markInProgress()" .-> BAPI
        GCT0 -. "fetch() · status = done" .-> BAPI
    end

    %% ---- External ticket systems (behind the adapters) ----
    LIN -. "@linear/sdk (GraphQL)" .-> EXTLIN[(Linear)]

    %% ---- Linear–GitHub integration: watches GitHub, updates Linear ----
    GH -. "PR merged / closed" .-> LGI["🔗 Linear–GitHub<br/>integration"]
    LGI -. "Mark Done" .-> EXTLIN

    G2 -.- T9([&lt;repo&gt;/.groundcrew/<br/>config.json])
    G3 -.- T10([with bypass<br/>perms on])
    G9 -.-> GH

    %% ---- Off-flow: Create PR pushes the branch to GitHub ----
    G8 -.-> GH(["🐙 &nbsp; GitHub &nbsp;<br/>code review &amp; merge service"])

    %% ============ HUMAN STAGE ============
    G9 --> H1[Human Review]
    H1 -- Fix --> TMUX
    H1 --> H2[Merge]
    H2 -.-> GH

    H1 -.-> GH

    %% ============ STYLES ============
    classDef step fill:#dbe9fb,stroke:#6b9bd1,color:#1a2b3c;
    classDef tool fill:#ffffff,stroke:#999,color:#333,font-style:italic;
    classDef db fill:#f5f5f5,stroke:#888,color:#333;

    class G0,P1,G1,G2,GMIP,G3,G4,G5,G6,G7,G8,G9,GCT0,GCT,H1,H2 step;
    class T9,T10 tool;

    classDef service fill:#e6e8ea,stroke:#aab1b8,color:#2a2f34,font-size:15px,font-weight:bold,padding:8px;
    class GH service;

    classDef integ fill:#dcefe6,stroke:#84b59c,color:#1f3a2e,font-weight:bold;
    class LGI integ;

    classDef ext fill:#f5f5f5,stroke:#888,color:#333,font-size:20px,font-weight:bold,padding:12px;
    class EXTLIN ext;

    classDef board fill:#ece3fb,stroke:#8b6fc1,color:#2a1f3c;
    class BAPI,LIN,SH board;

    style SAFEHOUSE fill:#ffe3c2,stroke:#e08a2e,color:#3a2410;
    style TMUX fill:#d6f5d6,stroke:#5aa55a,color:#1a2b1a;
    style BOARD fill:#f3eefc,stroke:#8b6fc1,color:#2a1f3c;
    style REG fill:#e3d9f5,stroke:#7a5fb0,color:#2a1f3c;
    style CLEAN fill:#eef2f7,stroke:#9aa7b5,color:#2a323c;
```
