"""Consumer-scoped Qlib payload checks; hashes are local integrity, not authentication."""
from __future__ import annotations
from datetime import date, datetime, timezone
from pathlib import Path
import math
import struct
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ConsumerDataContract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    consumer: str = Field(min_length=1)
    instruments: tuple[str, ...] = Field(min_length=1)
    start: date
    end: date
    required_fields: tuple[str, ...] = ('close',)
    # Explicitly verified non-trading observations only; never blanket imputation.
    allowed_missing: dict[str, tuple[date, ...]] = Field(default_factory=dict)
    @model_validator(mode='after')
    def valid(self):
        import re
        if self.start > self.end or not self.required_fields or len(set(self.instruments))!=len(self.instruments):
            raise ValueError('INVALID_CONSUMER_CONTRACT')
        if any(not re.fullmatch(r'(SH|SZ|BJ)\d{6}', x, re.I) for x in self.instruments):
            raise ValueError('INVALID_CONSUMER_INSTRUMENT')
        if any(not re.fullmatch(r'[a-z][a-z0-9_]*', x) for x in self.required_fields):
            raise ValueError('INVALID_CONSUMER_FIELD')
        return self

def validate_qlib_consumer(root, contract: ConsumerDataContract):
    root=Path(root)
    sessions=[date.fromisoformat(x.strip()) for x in (root/'calendars/day.txt').read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    records={}
    for line in (root/'instruments/all.txt').read_text(encoding='utf-8-sig').splitlines():
        iid,start,end=line.split(); records.setdefault(iid.upper(),[]).append((date.fromisoformat(start),date.fromisoformat(end)))
    selected=[(i,d) for i,d in enumerate(sessions) if contract.start<=d<=contract.end]
    report={'scope':'CONSUMER_CONTENT','consumer':contract.consumer,'contract':contract.model_dump(mode='json'),
            'validated_at':datetime.now(timezone.utc).isoformat(),'status':'VALID','activation_allowed':True,'instruments':{}}
    if not selected:
        report.update(status='INVALID',activation_allowed=False,reason='CONSUMER_WINDOW_EMPTY'); return report
    valid_symbols=0
    for iid in contract.instruments:
        ranges=records.get(iid.upper(),[])
        indices=[(i,d) for i,d in selected if any(s<=d<=e for s,e in ranges)]
        reasons=[]; fields={}
        if not ranges: reasons.append('SECURITY_MEMBERSHIP_MISSING')
        for name in contract.required_fields:
            f=root/'features'/iid.lower()/(name+'.day.bin'); available={}
            if f.is_file():
                raw=f.read_bytes()
                if len(raw)>=8 and len(raw)%4==0:
                    vals=struct.unpack('<'+'f'*(len(raw)//4),raw)
                    if math.isfinite(vals[0]) and vals[0]>=0 and vals[0]==int(vals[0]) and int(vals[0])+len(vals)-1<=len(sessions):
                        available=dict(enumerate(vals[1:],int(vals[0])))
            required=[(i,d) for i,d in indices if d not in contract.allowed_missing.get(iid,())]
            observed=sum(i in available and math.isfinite(available[i]) and (available[i]>0 if name in ('open','high','low','close') else True) for i,d in required)
            fields[name]={'expected':len(required),'observed':observed,'legal_missing':len(indices)-len(required)}
            if not f.is_file(): reasons.append('REQUIRED_FIELD_MISSING:'+name)
            elif required and observed==0: reasons.append('NO_USABLE_VALUES:'+name)
            elif observed<len(required): reasons.append('INCOMPLETE_WINDOW:'+name)
        # Securities outside their listing interval need no invented observations.
        status='VALID' if not reasons else 'INVALID'
        valid_symbols+=status=='VALID'
        report['instruments'][iid]={'status':status,'reasons':reasons,'fields':fields}
    if valid_symbols<len(contract.instruments):
        report.update(status='PARTIAL' if valid_symbols else 'INVALID',activation_allowed=False)
    return report

def default_close_contract(root):
    root=Path(root)
    sessions=[date.fromisoformat(x.strip()) for x in (root/'calendars/day.txt').read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    instruments=tuple(sorted({x.split()[0].upper() for x in (root/'instruments/all.txt').read_text().splitlines() if x.strip()}))
    return ConsumerDataContract(consumer='CLOSE_RESEARCH_ONLY',instruments=instruments,start=sessions[0],end=sessions[-1])
