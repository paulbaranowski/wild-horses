# SDLC Workflow

Planning phases (PRD + ERD/task breakdown). Once tasks are written to the ticket
system, the autonomous-execution phase is documented separately in
[Groundcrew — Autonomous Execution](./groundcrew-workflow.md).

```mermaid
flowchart TD
    %% ============ PHASE 1: PRD ============
    subgraph PRD["📄 PRD Creation"]
        direction TB
        A1[Create Feature<br/>ticket / doc] --> A2[Create<br/>requirements]
        A2 --> A3[Save to disk]
        A3 --> A4[Review]
        A4 --> A5[Attach PRD<br/>to ticket]
    end

    A1 -.- T1([/superpowers])
    A3 -.- T2([/plan-save])
    A4 -.- T3([/plannotator])
    A5 -.- T4([/plan-jira])

    %% ============ PHASE 2: ERD + TASK SPLIT ============
    A5 --> B1
    subgraph ERD["🧩 ERD &amp; Task Breakdown"]
        direction TB
        B1[Write ERD] --> B2[Write to disk]
        B2 --> B3[Review]
        B3 --> B4[Split into tasks]
        B4 --> C1[Draft Implementation Plan] --> D1[Write to ticket system]
        B4 --> C2[Draft Implementation Plan] --> D2[Write to ticket system]
        B4 --> C3[Draft Implementation Plan] --> D3[Write to ticket system]
    end

    B1 -.- T5([/superpowers])
    B2 -.- T6([/plan-save])
    B3 -.- T7([/plannotator])
    B4 -.- T8([/to-issues])
    C1 -.- T17([/superpowers])
    C2 -.- T18([/superpowers])
    C3 -.- T19([/superpowers])
    D1 -.- T20([/plan-save or<br/>/plan-jira])
    D2 -.- T21([/plan-save or<br/>/plan-jira])
    D3 -.- T22([/plan-save or<br/>/plan-jira])
    D2 -.- TS1[(Ticket<br/>System)]

    %% ============ HANDOFF TO GROUNDCREW ============
    D1 --> HX
    D2 --> HX
    D3 --> HX
    HX["▶ Groundcrew picks up the tickets<br/>(see groundcrew-workflow.md)"]

    %% ============ STYLES ============
    classDef step fill:#dbe9fb,stroke:#6b9bd1,color:#1a2b3c;
    classDef tool fill:#ffffff,stroke:#999,color:#333,font-style:italic;
    classDef db fill:#f5f5f5,stroke:#888,color:#333;
    classDef handoff fill:#fff4cf,stroke:#d4a929,color:#3a3210;

    class A1,A2,A3,A4,A5,B1,B2,B3,B4,C1,C2,C3,D1,D2,D3 step;
    class T1,T2,T3,T4,T5,T6,T7,T8,T17,T18,T19,T20,T21,T22 tool;
    class TS1 db;
    class HX handoff;
```
