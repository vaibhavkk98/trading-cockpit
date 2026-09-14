# Systematic Engine V1A — Edge Capture Engine

## Frozen policies

- E0: existing `SHADOW_ROLLING`, Rolling Admission + C3 + H10.
- E1: `SHADOW_EDGE_H20`, identical upstream contract with H20 time exit.
- E2: `SHADOW_EDGE_H20_CATASTROPHE`, H20 plus the frozen wider-of 3.5 entry ATR or 12% disaster threshold.
- E3: `SHADOW_EDGE_H20_THESIS`, H20 plus the frozen slow thesis-failure state machine.

| Policy | Methodology hash | Config hash |
| --- | --- | --- |
| E1 | `e5184c73a2d7831bbcfc30691e2ab8b4cb89cfceeebdf5ece89f94bc7f40bc7b` | `3c7efef446d6c7309514fb9fd03a5cb864abad80fcfc8125aac7d4d50f32dfff` |
| E2 | `9606cefbf058680fdae9464a0ad9625e39dc1c57b96654a4579ab4f3cec8bcd0` | `2269e96fe9fb42167885854d5d357ff285181fc3a3e2c982837a174b9c9b5246` |
| E3 | `2dabdeb7556ebdf746aeb541aefbd59cfe95de06f220900a11981bb984e9ac13` | `0d77e3fb83d41a8b44373d05dd267ce237e984aa1c94b08149f2cf0f12adf048` |

Thesis rule version: `EDGE_THESIS_FAILURE_V1`.

All accounts are paper-only, independently funded with ₹10L, target nine positions (ten hard maximum), use P0/C3/T+1/frozen costs, retain the existing queue expiry, and prohibit replacement.

## Thesis failure

After three observed sessions, T is a completed-close break below a declining EMA20; R is five-session stock-minus-NIFTY500 return at or below -3 percentage points; V is a negative close with volume ratio at least 1.5 and close location at most 0.35. At least two evaluable components must be true on two consecutive completed sessions. Execution is submitted for the next executable open. Missingness cannot trigger an exit.

## Attribution

`edge_capture_matches` stores one actual account leg per originating signal and never fabricates equal entries. `edge_capture_telemetry` records capital duration and capacity divergence. Early protective exits are observed to their original H20 horizon for exit regret; no observation affects decisions.
