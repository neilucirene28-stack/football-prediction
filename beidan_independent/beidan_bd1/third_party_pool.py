"""Parse source-native observations from the 500 WDL HTML, without ID approval.

Sale cutoff, clock-only kickoff, WDL line, reference SP and average odds are
separate fields. No JavaScript is evaluated and no kickoff date is invented.
"""
from html.parser import HTMLParser
from datetime import datetime
import re


def _object(value):
    if not isinstance(value, str) or not value.startswith('{') or not value.endswith('}'):
        raise ValueError('row metadata object missing')
    text = value[1:-1]; fields = {}; offset = 0
    token = re.compile(r"\s*([A-Za-z_]\w*)\s*:\s*(?:'([^'\\]*)'|(-?\d+))\s*(,|$)")
    while offset < len(text):
        match = token.match(text, offset)
        if match is None or match[1] in fields:
            raise ValueError('unsupported or duplicate row metadata')
        fields[match[1]] = match[2] if match[2] is not None else int(match[3])
        offset = match.end()
    return fields


class _Pool(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows=[]; self.row=None; self.periods=[]; self.selected_periods=[]
        self.in_period_select=False; self.side=None; self.span=None

    def handle_starttag(self, tag, attrs):
        a=dict(attrs); classes=set(a.get('class','').split())
        if tag=='select' and a.get('id')=='expect_select': self.in_period_select=True
        if tag=='option' and self.in_period_select and 'selected' in a:
            self.selected_periods.append(a.get('value'))
        if tag=='input' and a.get('id')=='expect': self.periods.append(a.get('value'))
        if tag=='tr' and 'vs_lines' in classes:
            if self.row is not None: raise ValueError('nested match row')
            metadata=_object(a.get('value')); seq=metadata.get('index'); fid=a.get('fid')
            if (not isinstance(seq,str) or not seq.isdigit() or int(seq)<1
                    or not isinstance(fid,str) or not fid.isdigit() or int(fid)<1):
                raise ValueError('invalid native pool sequence or event ID')
            self.row={'seq':int(seq),'provider_match_id':fid,'metadata':metadata,
                      'reference_sp_observed':[], 'average_odds_observed':[], 'teams':{}}
        if self.row is None:return
        if tag=='td':
            self.side='home' if 'tr' in classes else ('away' if 'tl' in classes else None)
            title=a.get('title','')
            if title.startswith('比赛时间：'):
                if 'kickoff_clock_only' in self.row: raise ValueError('duplicate kickoff clock')
                self.row['kickoff_clock_only']=title.split('：',1)[1]
        if tag=='a' and self.side:
            match=re.fullmatch(r'https://liansai\.500\.com/team/(\d+)/',a.get('href',''))
            if match:
                if self.side in self.row['teams']:raise ValueError('duplicate source team link')
                self.row['teams'][self.side]={'id':match[1],'title':a.get('title'),'display_name':''}
                self.link_side=self.side
        if tag=='a':
            match=re.fullmatch(r'https://liansai\.500\.com/zuqiu-(\d+)/',a.get('href',''))
            if match:self.row['provider_competition_id']=match[1]
        if tag=='span' and ('sp_value' in classes or 'pjoz' in classes):
            self.span=('reference_sp_observed' if 'sp_value' in classes else 'average_odds_observed','')

    def handle_data(self,data):
        if self.row is None:return
        if self.span is not None:self.span=(self.span[0],self.span[1]+data)
        if getattr(self,'link_side',None):self.row['teams'][self.link_side]['display_name']+=data

    def handle_endtag(self,tag):
        if tag=='select': self.in_period_select=False
        if tag=='span' and self.span is not None and self.row is not None:
            self.row[self.span[0]].append(self.span[1].strip());self.span=None
        if tag=='a':self.link_side=None
        if tag=='td':self.side=None
        if tag=='tr' and self.row is not None:
            m=self.row['metadata']; line=m.get('rangqiuNum')
            if not isinstance(line,str) or not re.fullmatch(r'[+-]?\d+',line):
                raise ValueError('WDL line is not an explicit integer')
            if set(self.row['teams'])!={'home','away'} or self.row['teams']['home']['id']==self.row['teams']['away']['id']:
                raise ValueError('source native sides missing or identical')
            if not re.fullmatch(r'\d{2}:\d{2}',self.row.get('kickoff_clock_only','')):
                raise ValueError('explicit source kickoff clock missing')
            datetime.strptime(self.row['kickoff_clock_only'], '%H:%M')
            datetime.strptime(m.get('endTime'), '%Y-%m-%d %H:%M')
            self.row.update(wdl_handicap_observed=int(line),sale_cutoff_local=m.get('endTime'),
                            kickoff_at=None, canonical_identity_approved=False,
                            official_source=False, sport_independently_verified=False,
                            model_imported=False, production_eligible=False)
            self.rows.append(self.row);self.row=None


def parse_pool_html(raw):
    # Strict GBK decode preserves characters instead of silently replacing bytes.
    text=raw.decode('gbk'); parser=_Pool();parser.feed(text);parser.close()
    if parser.row is not None or len(parser.periods)!=1 or parser.selected_periods!=parser.periods:
        raise ValueError('period input and current selected period differ')
    if (not parser.rows or len({r['seq'] for r in parser.rows})!=len(parser.rows)
            or len({r['provider_match_id'] for r in parser.rows})!=len(parser.rows)):
        raise ValueError('empty or duplicate source pool sequence')
    for row in parser.rows:row['period']=parser.periods[0]
    return parser.rows, {'period':parser.periods[0], 'rows_n':len(parser.rows),
                         'decoding':'strict_gbk', 'official_source':False,
                         'source_kickoff_date_explicit':False, 'model_imported_n':0}
