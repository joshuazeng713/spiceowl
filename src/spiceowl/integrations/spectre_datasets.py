"""Bounded, original PSF ASCII reader for AC, DC and structured dcOpInfo.

No files, commands or expressions are executed. This is a finite grammar, not
the complete Cadence format or a binary PSF/PSFXL reader. Public tests are authored
from format observations, not copied simulator outputs or upstream parser code.
"""
import hashlib
import io
import json
import math
import re

from .spectre_results import PsfError, MAX_PSF_BYTES, MAX_SAMPLES

SCHEMA = 'spiceowl.spectre-dataset.v1'
MAX_METADATA = 2*1024*1024
MAX_TRACES = 512
MAX_TYPES = 256
MAX_FIELDS = 1024
MAX_DEVICES = 4096
MAX_VALUES = 2_000_000
TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)|([-+]?(?:nan|inf(?:inity)?))|([A-Za-z_][A-Za-z_0-9]*)|([()*]))', re.I)


class Tokens:
    def __init__(self, text): self.text=text; self.offset=0; self.saved=None; self.count=0
    def peek(self):
        if self.saved is None:
            if self.offset>=len(self.text): return None
            match=TOKEN.match(self.text,self.offset)
            if not match:
                if not self.text[self.offset:].strip():self.offset=len(self.text);return None
                raise PsfError('Unsupported token in PSF ASCII.')
            self.offset=match.end(); self.count+=1
            if self.count>MAX_VALUES*5: raise PsfError('PSF token limit exceeded.','psf_limit')
            quoted,number,special,word,bracket=match.groups()
            if quoted is not None:
                try:value=json.loads(quoted)
                except ValueError:raise PsfError('Invalid quoted PSF text.') from None
                if len(value)>4096 or any(ord(c)<32 for c in value):raise PsfError('PSF string exceeds supported bounds.','psf_limit')
                self.saved=('string',value)
            elif number is not None:
                value=float(number);self.saved=('number',value if math.isfinite(value) else None)
            elif special is not None:self.saved=('number',None)
            elif word is not None:self.saved=('word',word)
            else:self.saved=('bracket',bracket)
        return self.saved
    def pop(self, expected=None):
        token=self.peek();self.saved=None
        if token is None or expected is not None and token!=expected:raise PsfError('Incomplete or unexpected PSF token.')
        return token
    def string(self):
        token=self.pop()
        if token[0]!='string':raise PsfError('Expected a quoted PSF name.')
        return token[1]


def _props(tokens):
    if tokens.peek()!=('word','PROP'):return {}
    tokens.pop();tokens.pop(('bracket','('));out={}
    while tokens.peek()!=('bracket',')'):
        name=tokens.string();value=tokens.pop()
        if name in out or value[0] not in ('string','number') or len(out)>=256:raise PsfError('Ambiguous or oversized PSF properties.')
        out[name]=value[1]
    tokens.pop();return out


def _definition(tokens, depth=0):
    if depth>2:raise PsfError('Nested PSF structure exceeds supported depth.','psf_unsupported')
    first=tokens.pop(); fields={}
    if first==('word','STRUCT'):
        tokens.pop(('bracket','('));kind='struct'
        while tokens.peek()!=('bracket',')'):
            name=tokens.string()
            if name in fields or len(fields)>=MAX_FIELDS:raise PsfError('Duplicate or excessive PSF structure fields.','psf_limit')
            fields[name]=_definition(tokens,depth+1)
        tokens.pop()
    elif first in (('word','FLOAT'),('word','COMPLEX')):
        tokens.pop(('word','DOUBLE'));kind='real' if first[1]=='FLOAT' else 'complex'
    elif first==('word','INT'):
        if tokens.pop() not in (('word','BYTE'),('word','SHORT'),('word','LONG')):
            raise PsfError('Unsupported native integer width.','psf_unsupported')
        kind='integer'
    elif first==('word','STRING'):
        tokens.pop(('bracket','*'));kind='string'
    else:raise PsfError('Only FLOAT DOUBLE, COMPLEX DOUBLE, INT LONG, STRING * and bounded STRUCT types are supported.','psf_unsupported')
    props=_props(tokens);unit=props.get('units')
    if unit is not None and (not isinstance(unit,str) or len(unit)>64):raise PsfError('Invalid native unit metadata.')
    return {'kind':kind,'unit':unit,'fields':fields,'properties':props}


def _sections(text):
    if isinstance(text,bytes):
        raw=text
        try:text=raw.decode('utf-8')
        except UnicodeError:raise PsfError('PSF bytes must be UTF-8 ASCII-format text.') from None
    elif isinstance(text,str):
        try:raw=text.encode('utf-8')
        except UnicodeError:raise PsfError('Invalid PSF Unicode text.') from None
    else:raise PsfError('Supply PSF text or exact UTF-8 bytes.')
    if len(raw)>MAX_PSF_BYTES:raise PsfError('PSF ASCII exceeds its size budget.','psf_limit')
    if not text.strip():raise PsfError('No PSF data supplied.','psf_missing')
    if '\x00' in text:raise PsfError('Binary PSF/PSFXL is unsupported.','psf_unsupported')
    names=['HEADER','TYPE','SWEEP','TRACE','VALUE','END'];sections={};current=None;metadata=0
    for line in io.StringIO(text):
        stripped=line.strip()
        if stripped in names:
            if stripped in sections or sections and names.index(stripped)<=names.index(current):raise PsfError('Duplicate or out-of-order PSF section.')
            current=stripped;sections[current]=io.StringIO()
        elif stripped:
            if current is None or current=='END':raise PsfError('Unexpected data outside PSF sections.')
            if current!='VALUE':
                metadata+=len(line.encode('utf-8'))
                if metadata>MAX_METADATA:raise PsfError('PSF metadata exceeds its budget.','psf_limit')
            sections[current].write(line)
    if not {'HEADER','TYPE','VALUE','END'}<=set(sections):raise PsfError('PSF is missing a required section or END marker.')
    return {key:value.getvalue() for key,value in sections.items()},hashlib.sha256(raw).hexdigest()


def _header(text):
    tokens=Tokens(text);out={};duplicates=[]
    identities={'analysis type','analysis name','AnalysisType','AnalysisName','simulator','version','PSF sweep points'}
    while tokens.peek() is not None:
        name=tokens.string();value=tokens.pop()
        if value[0] not in ('string','number'):raise PsfError('Invalid PSF header value.')
        if name in out:
            if name in identities:raise PsfError('Ambiguous PSF analysis identity.','psf_identity')
            duplicates.append(name)
        out[name]=value[1]
    return out,sorted(set(duplicates))


def _types(text):
    tokens=Tokens(text);out={}
    while tokens.peek() is not None:
        name=tokens.string()
        if name in out or len(out)>=MAX_TYPES:raise PsfError('Duplicate or excessive PSF types.','psf_limit')
        out[name]=_definition(tokens)
    return out


def _references(text, types, *, groups=False):
    tokens=Tokens(text);signals={};order={}
    def signal(name):
        typename=tokens.string()
        if typename not in types or types[typename]['kind'] not in ('real','complex','integer'):
            raise PsfError('Unsupported or undeclared swept trace type.','psf_unsupported')
        if name in signals or name in order or not 1<=len(name)<=256 or len(signals)>=MAX_TRACES:
            raise PsfError('Ambiguous or excessive PSF trace identities.','psf_limit')
        props={**types[typename]['properties'],**_props(tokens)}
        unit=props.get('units')
        if unit is not None and (not isinstance(unit,str) or len(unit)>64):raise PsfError('Invalid trace unit.')
        signals[name]={'name':name,'type':typename,'kind':types[typename]['kind'],'unit':unit}
    while tokens.peek() is not None:
        name=tokens.string()
        if tokens.peek()==('word','GROUP'):
            if not groups or name in order or name in signals:raise PsfError('Unsupported or duplicate PSF group.','psf_unsupported')
            tokens.pop();count=tokens.pop()
            if count[0]!='number' or count[1] is None or count[1]!=int(count[1]) or not 1<=count[1]<=MAX_TRACES:
                raise PsfError('Invalid PSF group length.','psf_limit')
            members=[]
            for _ in range(int(count[1])):
                member=tokens.string();signal(member);members.append(member)
            order[name]=members
        else:
            signal(name);order[name]=[name]
    return signals,order


def _read_value(tokens, definition):
    kind=definition['kind']
    if kind=='struct':
        tokens.pop(('bracket','('))
        fields={name:_read_value(tokens,field) for name,field in definition['fields'].items()}
        tokens.pop(('bracket',')'));return fields
    if kind=='complex':
        tokens.pop(('bracket','('));real=tokens.pop();imag=tokens.pop();tokens.pop(('bracket',')'))
        if real[0]!='number' or imag[0]!='number':raise PsfError('Complex values require a real/imaginary numeric pair.')
        return [real[1],imag[1]]
    token=tokens.pop()
    if kind=='string':
        if token[0]!='string':raise PsfError('Expected native string value.')
    elif token[0]!='number':raise PsfError('Expected native numeric value.')
    if kind=='integer' and token[1] is not None and token[1]!=int(token[1]):raise PsfError('Nonintegral INT LONG value.')
    return token[1]


def _field(value, definition):
    if definition['kind']=='struct':raise PsfError('Nested operating-point structures are unsupported.','psf_unsupported')
    return {'value':value,'unit':definition['unit'],
            'status':'undefined' if value is None or isinstance(value,list) and None in value else 'valid',
            'value_kind':definition['kind']}


def parse_psf_analysis(text, *, analysis_kind, analysis_name=None):
    """Parse one explicitly selected native AC/DC/dcOpInfo ASCII artifact."""
    if analysis_kind not in ('ac','dc','dc_op'):raise PsfError('Choose ac, dc or dc_op explicitly.','psf_analysis')
    if analysis_name is not None and (not isinstance(analysis_name,str) or not 1<=len(analysis_name)<=256):
        raise PsfError('Invalid expected native analysis name.','psf_identity')
    sections,digest=_sections(text);header,duplicates=_header(sections['HEADER']);types=_types(sections['TYPE'])
    native_type=header.get('analysis type');native_name=header.get('analysis name')
    expected=(native_type=='ac' if analysis_kind=='ac' else native_type=='dc' if analysis_kind=='dc'
              else native_type in ('info','dcOp') and header.get('AnalysisType','dcOp' if native_type=='dcOp' else None)=='dcOp')
    if not expected or not isinstance(native_name,str) or not native_name or analysis_name is not None and native_name!=analysis_name:
        raise PsfError('Native analysis identity does not match the requested dataset.','psf_identity')
    dataset={'schema':SCHEMA,'format':'psf_ascii','raw_sha256':digest,
        'analysis':{'kind':analysis_kind,'name':native_name,'native_type':native_type,
                    'producer_name':header.get('AnalysisName'), 'producer_type':header.get('AnalysisType'),
                    'simulator':header.get('simulator'),'simulator_version':header.get('version')},
        'sample_count':0,'axis':None,'signals':[],'operating_points':[],
        'warnings':['duplicate_nonidentity_header:'+name for name in duplicates],
        'specification_result':'not_evaluated'}
    tokens=Tokens(sections['VALUE']);undefined=False;total=0
    if 'SWEEP' in sections:
        if analysis_kind=='dc_op' or 'TRACE' not in sections:raise PsfError('Unexpected swept operating-point or absent trace section.','psf_unsupported')
        sweeps,_=_references(sections['SWEEP'],types)
        if len(sweeps)!=1:raise PsfError('Only one explicit sweep dimension is supported.','psf_unsupported')
        axis=next(iter(sweeps.values()));axis_name=axis['name']
        if axis['kind'] not in ('real','integer'):raise PsfError('Sweep axis must be real.','psf_unsupported')
        if analysis_kind=='ac' and axis['unit']!='Hz':raise PsfError('AC requires explicit native frequency units Hz.','psf_unit')
        signals,order=_references(sections['TRACE'],types,groups=True)
        if not signals or axis_name in signals or axis_name in order:raise PsfError('Missing or ambiguous swept traces.')
        values={name:[] for name in signals};axis_values=[];sample={}
        def finish():
            nonlocal total,undefined
            if set(sample)!=set(signals)|{axis_name}:raise PsfError('Missing or duplicated swept sample values.','psf_unaligned')
            x=sample[axis_name]
            if x is None:raise PsfError('Sweep axis cannot be nonfinite.','psf_nonfinite')
            if len(axis_values)>=MAX_SAMPLES:raise PsfError('Too many swept samples.','psf_limit')
            if analysis_kind=='ac' and (x<0 or axis_values and x<=axis_values[-1]):raise PsfError('Frequency must be nonnegative and strictly increasing.','psf_frequency_nonmonotonic')
            if analysis_kind=='dc' and axis_values and x==axis_values[-1]:raise PsfError('Repeated DC sweep axis point.','psf_axis_nonmonotonic')
            axis_values.append(x)
            for name in signals:
                value=sample[name];values[name].append(value)
                undefined |= value is None or isinstance(value,list) and None in value
                total+=2 if isinstance(value,list) else 1
            if total>MAX_VALUES:raise PsfError('Dataset value budget exceeded.','psf_limit')
        while tokens.peek() is not None:
            name=tokens.string()
            if name==axis_name:
                if sample:finish()
                sample={axis_name:_read_value(tokens,types[axis['type']])}
            else:
                if axis_name not in sample or name not in order:raise PsfError('Unknown swept value or absent axis.','psf_unaligned')
                for member in order[name]:
                    if member in sample:raise PsfError('Duplicated swept signal.','psf_unaligned')
                    sample[member]=_read_value(tokens,types[signals[member]['type']])
        if not sample:raise PsfError('PSF has no samples.','psf_empty')
        finish()
        if analysis_kind=='dc' and len(axis_values)>2:
            direction=axis_values[1]>axis_values[0]
            if any((b>a)!=direction for a,b in zip(axis_values,axis_values[1:])):raise PsfError('DC axis must be strictly monotonic.','psf_axis_nonmonotonic')
        declared=header.get('PSF sweep points')
        if declared is not None and declared!=len(axis_values):raise PsfError('Declared sample count disagrees with data.','psf_unaligned')
        dataset.update(sample_count=len(axis_values),axis={'name':axis_name,'unit':axis['unit'],'values':axis_values})
        for name,signal in signals.items():
            row={'name':name,'unit':signal['unit'],'value_kind':signal['kind'],'native_type':signal['type']}
            if signal['kind']=='complex':row.update(real=[v[0] for v in values[name]],imag=[v[1] for v in values[name]])
            else:row['values']=values[name]
            dataset['signals'].append(row)
    else:
        if analysis_kind=='ac' or 'TRACE' in sections:raise PsfError('Unswept AC or unexplained trace declarations are unsupported.','psf_unsupported')
        names=set()
        while tokens.peek() is not None:
            name=tokens.string();typename=tokens.string()
            if name in names or typename not in types or len(names)>=MAX_DEVICES:raise PsfError('Ambiguous, undeclared or excessive operating-point records.','psf_limit')
            names.add(name);definition=types[typename];value=_read_value(tokens,definition);properties=_props(tokens)
            if definition['kind']=='struct':
                parameters={field:_field(value[field],desc) for field,desc in definition['fields'].items()}
                total+=len(parameters);undefined |= any(p['status']=='undefined' for p in parameters.values())
                dataset['operating_points'].append({'name':name,'model_type':typename,
                    'model':properties.get('model'),'parameters':parameters})
            else:
                field=_field(value,definition);total+=1;undefined |= field['status']=='undefined'
                if definition['kind']=='complex':
                    dataset['signals'].append({'name':name,'unit':definition['unit'],'value_kind':'complex','real':[value[0]],'imag':[value[1]],'native_type':typename})
                else:dataset['signals'].append({'name':name,'unit':definition['unit'],'value_kind':definition['kind'],'values':[value],'native_type':typename})
            if total>MAX_VALUES:raise PsfError('Operating-point value budget exceeded.','psf_limit')
        if not names:raise PsfError('PSF contains no operating-point values.','psf_empty')
        dataset['sample_count']=1
    dataset['has_undefined_values']=bool(undefined)
    if undefined:dataset['warnings'].append('native_nonfinite_values_preserved_as_undefined_null')
    return dataset
