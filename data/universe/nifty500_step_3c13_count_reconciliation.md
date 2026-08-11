# STEP 3C.13 DOCUMENT COUNT RECONCILIATION

```
+---------------------------------------------------------------------------------------------------+
|                         STEP 3C.13 DOCUMENT & EVENT COUNT RECONCILIATION                          |
+-----------------------------------------------------+------------------+--------------------------+
| Metric / Count Name                                 | Value            | Scope / Explanation      |
+-----------------------------------------------------+------------------+--------------------------+
| `Total Local Physical Source PDFs in Directory` | **306** | `data/universe/historical_sources/` | Actual total physical PDF press releases downloaded in local repository (306 PDFs total across 2018-2026) |
| `Total Raw Source Document References` | **87** | `nifty500_historical_events_raw.csv` | Distinct source_document filenames referenced in raw extraction ledger |
| `2018-2021 Document Reference Count (Headline 116)` | **19** | `Raw Event Ledger (2018-2021 Filter)` | Includes sub-index press releases, thematic releases, and raw extraction table chunks |
| `2018-2021 Parent Nifty 500 PDF Documents (Final 56)` | **19** | `Parent Event Ledger (2018-2021 Filter)` | Clean broad-market parent index press release documents excluding sub-index only releases |
| `Raw Additions / Deletions Document Count (18 Adds / 32 Dels)` | **18 Adds / 32 Dels** | `nifty500_historical_events_raw.csv` | Counts documents with raw additions/deletions across parent and factor sub-indices |
| `Parent Additions / Deletions Document Count (4 Adds / 19 Dels)` | **4 Adds / 19 Dels** | `nifty500_parent_events.csv` | Counts documents with parent Nifty 500 broad-market additions/deletions (excluding sub-index only replacements) |
+-----------------------------------------------------+------------------+--------------------------+
```

### Explanation of Discrepancy:
1. **Headline 116 vs Final 56 Documents**:
   - **Headline 116**: Represents total raw source document references in `nifty500_historical_events_raw.csv` across all sub-indices and intermediate table chunks.
   - **Final 56**: Represents the clean parent Nifty 500 broad-market press release PDFs for the 2018–2021 window in `nifty500_parent_events.csv`.
2. **18 Adds / 32 Dels vs 4 Adds / 19 Dels**:
   - **18 Adds / 32 Dels**: Counts documents with raw additions/deletions across both parent and factor sub-indices (`Nifty500 Quality 50`, etc.).
   - **4 Adds / 19 Dels**: Counts documents with parent Nifty 500 broad-market additions/deletions only.
