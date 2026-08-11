"""Single public BSE corporate-announcement provider for P3A.2."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import requests

# Deliberately small MVP mapping: unsupported symbols report NOT_AVAILABLE.
BSE_SCRIP = {"RELIANCE":"500325","TCS":"532540","INFY":"500209","HDFCBANK":"500180","ICICIBANK":"532174","TATAMOTORS":"500570","SUNPHARMA":"524715","NTPC":"532555","BEL":"500049","TATASTEEL":"500470"}
URL="https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"

def fetch_bse_announcements(symbol: str, cutoff: datetime, lookback_days: int = 7) -> Tuple[bool, List[Dict[str, Any]]]:
    scrip=BSE_SCRIP.get(symbol.replace('.NS',''))
    if not scrip: return False, []
    start=(cutoff-timedelta(days=lookback_days)).strftime('%Y%m%d'); end=cutoff.strftime('%Y%m%d')
    params={"strCat":"-1","strPrevDate":start,"strScrip":scrip,"strSearch":"P","strToDate":end,"strType":"C"}
    try:
        response=requests.get(URL,params=params,headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.bseindia.com/"},timeout=12)
        if response.status_code != 200: return False, []
        payload=response.json() if response.text.lstrip().startswith(('{','[')) else []
        rows=payload.get('Table',[]) if isinstance(payload,dict) else []
        records=[]
        for row in rows:
            stamp=row.get('DissemDT') or row.get('NewsDt') or ''
            try: published=datetime.strptime(stamp[:19],'%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            except Exception: continue
            if published>cutoff: continue
            ident=str(row.get('NEWSID') or row.get('SCRIP_CD') or '')
            records.append({"symbol":symbol,"pub_date":published.strftime('%a, %d %b %Y %H:%M:%S +0000'),"source":"BSE corporate announcement","source_type":"EXCHANGE_FILING","url":f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{row.get('ATTACHMENTNAME','')}","headline":row.get('NEWSSUB',''),"document_id":ident,"summary":row.get('SLONGNAME') or row.get('NEWSSUB',''),"evidence":f"BSE announcement ID {ident}; category {row.get('CATEGORYNAME','NOT_AVAILABLE')}."})
        return True, records
    except Exception: return False, []
