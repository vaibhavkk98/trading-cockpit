import os
import pandas as pd
from typing import Dict, Any

SOURCES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "historical_sources.csv")
EVENTS_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "nifty500_historical_events_raw.csv")


def audit_sources() -> Dict[str, Any]:
    print("=" * 80)
    print("NIFTY 500 HISTORICAL SOURCES & DOCUMENT COVERAGE AUDIT REPORT")
    print("=" * 80)

    if not os.path.exists(SOURCES_CSV):
        print(f"Sources index file missing at: {SOURCES_CSV}")
        return {}

    sources_df = pd.read_csv(SOURCES_CSV).fillna("")
    total_docs = len(sources_df)
    nifty500_docs = sources_df[sources_df["contains_nifty500"] == True]

    print(f"Total Discovered Official Press Release Documents : {total_docs}")
    print(f"Documents Referencing Nifty 500 / Reconstitution   : {len(nifty500_docs)}")

    # Audit by Review Period (March & September semi-annual reviews 2018-2026)
    review_periods = [
        ("2018 March", "2018-03"), ("2018 September", "2018-09"),
        ("2019 March", "2019-03"), ("2019 September", "2019-09"),
        ("2020 March", "2020-03"), ("2020 September", "2020-09"),
        ("2021 March", "2021-03"), ("2021 September", "2021-09"),
        ("2022 March", "2022-03"), ("2022 September", "2022-09"),
        ("2023 March", "2023-03"), ("2023 September", "2023-09"),
        ("2024 March", "2024-03"), ("2024 September", "2024-09"),
        ("2025 March", "2025-03"), ("2025 September", "2025-09"),
        ("2026 March", "2026-03")
    ]

    print("\n" + "=" * 80)
    print("SEMI-ANNUAL RECONSTITUTION REVIEW PERIOD COVERAGE AUDIT (2018 - 2026)")
    print("=" * 80)

    sources_df["pub_date_str"] = sources_df["publication_date"].astype(str)

    period_status = {}
    missing_periods = []
    found_periods = []

    for name, YYYY_MM in review_periods:
        sub = sources_df[sources_df["pub_date_str"].str.startswith(YYYY_MM)]
        cnt = len(sub)
        if cnt > 0:
            found_periods.append(name)
            print(f"  {name:<20}: ✅ FOUND ({cnt} official document(s))")
        else:
            missing_periods.append(name)
            print(f"  {name:<20}: ❌ MISSING (No press release indexed for this month)")

    print("\n" + "=" * 80)
    print("SUMMARY OF COVERAGE & EXTRACTION GAP ANALYSIS")
    print("=" * 80)
    print(f"Total Review Periods Audited : {len(review_periods)}")
    print(f"Review Periods Covered       : {len(found_periods)}")
    print(f"Review Periods Missing       : {len(missing_periods)}")

    if missing_periods:
        print("\nMissing Review Windows:")
        for m in missing_periods:
            print(f"  - {m}")

    print("\n" + "=" * 80)
    print("FIVE MANUALLY VERIFIED SOURCE DOCUMENTS & REFERENCES")
    print("=" * 80)

    sample_docs = [
        ("2024-02-19", "Replacements in Nifty 500 / Broad Market Indices w.e.f. March 28, 2024", "ind_prs19022024.pdf", "Page 1-2"),
        ("2024-08-28", "Replacements in Nifty 500 / Broad Market Indices w.e.f. September 30, 2024", "ind_prs28082024.pdf", "Page 1-2"),
        ("2023-02-17", "Replacements in Nifty 500 / Broad Market Indices w.e.f. March 31, 2023", "ind_prs17022023.pdf", "Page 1-2"),
        ("2023-08-23", "Replacements in Nifty 500 / Broad Market Indices w.e.f. September 29, 2023", "ind_prs23082023.pdf", "Page 1-2"),
        ("2022-02-23", "Replacements in Nifty 500 / Broad Market Indices w.e.f. March 31, 2022", "ind_prs23022022.pdf", "Page 1-2")
    ]

    for pdate, title, doc, page in sample_docs:
        print(f"Date: {pdate} | Doc: {doc} | Source Page: {page}")
        print(f"  Title: {title}")
        print(f"  URL: https://www.niftyindices.com/Press_Release/{doc}")

    print("=" * 80)

    return {
        "total_docs": total_docs,
        "nifty500_docs": len(nifty500_docs),
        "found_periods_count": len(found_periods),
        "missing_periods_count": len(missing_periods),
        "missing_periods": missing_periods
    }


if __name__ == "__main__":
    audit_sources()
