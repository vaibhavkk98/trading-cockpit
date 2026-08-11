import os
import re
import zlib
import pandas as pd
from typing import List, Dict, Any

SOURCES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "historical_sources.csv")
SOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "historical_sources")
OUT_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "nifty500_historical_events_raw.csv")


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extracts text strings from a PDF file using standard library zlib decompression.
    """
    if not os.path.exists(pdf_path):
        return ""

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    text_pieces = []
    stream_blocks = re.findall(rb"stream[\r\n]+(.*?)[\r\n]+endstream", pdf_bytes, re.DOTALL)
    for chunk in stream_blocks:
        for wbits in [15, -15, 31, 47]:
            try:
                dec = zlib.decompress(chunk, wbits)
                matches = re.findall(rb"\((.*?)\)", dec)
                for m in matches:
                    try:
                        txt = m.decode("latin1", errors="ignore").strip()
                        if len(txt) > 1:
                            text_pieces.append(txt)
                    except Exception:
                        pass
                break
            except Exception:
                continue

    return " ".join(text_pieces)


def extract_events_from_sources() -> List[Dict[str, Any]]:
    """
    Parses downloaded official Nifty Indices press release PDFs and extracts explicit Nifty 500 events.
    """
    if not os.path.exists(SOURCES_CSV):
        return []

    sources_df = pd.read_csv(SOURCES_CSV)
    extracted_events = []

    for idx, row in sources_df.iterrows():
        fname = str(row["filename"]) if pd.notna(row["filename"]) else ""
        pdf_path = os.path.join(SOURCES_DIR, fname)
        if not os.path.exists(pdf_path):
            continue

        raw_txt = extract_text_from_pdf(pdf_path)
        if not raw_txt:
            continue

        url = str(row["url"]) if pd.notna(row["url"]) else ""
        pub_date = str(row["publication_date"]) if pd.notna(row["publication_date"]) else ""

        # Search for Nifty 500 references in PDF text
        if "Nifty 500" in raw_txt or "NIFTY 500" in raw_txt or "Nifty500" in raw_txt:
            # Extract effective date string if found
            eff_match = re.search(r"(?:w\.e\.f\.|effective|effective date|effective from)\s*([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})", raw_txt, re.IGNORECASE)
            eff_date_str = eff_match.group(1) if eff_match else pub_date

            # Parse additions/exclusions blocks from text
            # Look for stock symbols / lines following addition/exclusion headers
            add_blocks = re.findall(r"(?:Inclusion|Addition|Added).*?Nifty 500.*?(?:\n|(?=[A-Z]{3,}))", raw_txt, re.IGNORECASE)
            del_blocks = re.findall(r"(?:Exclusion|Deletion|Removed).*?Nifty 500.*?(?:\n|(?=[A-Z]{3,}))", raw_txt, re.IGNORECASE)

            # Record traceable event metadata
            status = "SUCCESS" if (add_blocks or del_blocks) else "NIFTY500_MENTIONED_NO_DIRECT_PARSED_BLOCK"
            
            extracted_events.append({
                "effective_date": eff_date_str,
                "event_type": "OFFICIAL_PRESS_RELEASE_NOTICE",
                "symbol": "",
                "company_name": "",
                "old_symbol": "",
                "new_symbol": "",
                "isin": "",
                "reason": str(row["title"]),
                "source_url": url,
                "source_document": fname,
                "source_publication_date": pub_date,
                "source_page": "unknown",
                "extraction_status": status
            })

    return extracted_events


def main():
    print("=" * 80)
    print("EXTRACTING SOURCE-TRACEABLE NIFTY 500 EVENTS FROM OFFICIAL DOCUMENTS")
    print("=" * 80)

    events = extract_events_from_sources()
    df_out = pd.DataFrame(events)

    if not df_out.empty:
        df_out.to_csv(OUT_CSV, index=False)
        print(f"Extracted {len(df_out)} source-traceable events. Saved to {OUT_CSV}")
        print(df_out.head(10))
    else:
        print("No events extracted yet.")

    print("=" * 80)


if __name__ == "__main__":
    main()
