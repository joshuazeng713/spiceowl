"""JSON/CSV command-line entry for offline measurement and CI."""
import argparse
import csv
import io
import json
from pathlib import Path
import sys

from . import AnalysisError, OPERATIONS, SCHEMA, analyze

MAX_FILE_BYTES = 64 * 1024 * 1024


def _json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise AnalysisError('invalid_json', 'Duplicate JSON object key.')
            result[key] = value
        return result
    def nonfinite(value): raise AnalysisError('invalid_json', 'Nonfinite JSON numbers are unsupported.')
    return json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)


def _read(path):
    if path == '-': raw = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    else:
        with Path(path).open('rb') as stream: raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES: raise AnalysisError('input_too_large', 'Input file exceeds 64 MiB.')
    return raw.decode('utf-8')


def _arguments(args):
    if args.request:
        if args.params or args.column: raise AnalysisError('ambiguous_input', '--request already contains all named arguments.')
        return _json(_read(args.request))
    if not args.params: raise AnalysisError('parameters_missing', '--input requires an explicit --params JSON file.')
    if args.params == '-' and args.input == '-':
        raise AnalysisError('ambiguous_input', 'Only one input may read stdin; provide a file for params or dataset.')
    parameters = _json(_read(args.params))
    if not isinstance(parameters, dict): raise AnalysisError('invalid_arguments', 'Parameters must be a JSON object.')
    text = _read(args.input)
    if args.format == 'json':
        data = _json(text)
        if not isinstance(data, dict): raise AnalysisError('invalid_dataset', 'JSON input must map explicitly named columns to arrays.')
    else:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise AnalysisError('invalid_csv', 'CSV requires unique named header columns.')
        data = {name: [] for name in reader.fieldnames}
        for row in reader:
            if set(row) != set(data): raise AnalysisError('invalid_csv', 'CSV row does not match its header.')
            for name in data: data[name].append(float(row[name]))
    if not args.column: raise AnalysisError('bindings_missing', 'Bind data explicitly with --column samples=output and/or other argument names.')
    for binding in args.column:
        name, separator, columns = binding.partition('=')
        if not separator or not name or name in parameters: raise AnalysisError('invalid_binding', 'Each input argument needs one distinct name=column binding.')
        selected = columns.split(',')
        if not all(column in data for column in selected): raise AnalysisError('missing_column', 'A bound dataset column is missing.')
        if len(selected) == 1: parameters[name] = data[selected[0]]
        else:
            lengths = {len(data[column]) for column in selected}
            if len(lengths) != 1: raise AnalysisError('sample_mismatch', 'Bound columns have different lengths.')
            parameters[name] = [list(row) for row in zip(*(data[column] for column in selected))]
    return parameters


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=OPERATIONS)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--request', help='JSON argument object, or - for stdin')
    source.add_argument('--input', help='CSV/JSON data file, or - for stdin')
    parser.add_argument('--params', help='Explicit parameter JSON object for --input')
    parser.add_argument('--format', choices=('csv', 'json'), default='csv')
    parser.add_argument('--column', action='append', help='Argument=column, or decisions=bit0,bit1,...')
    parser.add_argument('--output', help='Write the JSON report to this explicit path instead of stdout')
    args = parser.parse_args(argv)
    try:
        report = analyze(args.operation, _arguments(args))
        status = 0 if report['status'] == 'valid' else 3
    except (AnalysisError, ValueError, TypeError, OSError, OverflowError) as error:
        report = {'schema': SCHEMA, 'operation': args.operation, 'status': 'invalid', 'metrics': {},
                  'warnings': [], 'error': {'code': getattr(error, 'code', 'invalid_input'), 'message': str(error)}}
        status = 2
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'
    if args.output:
        try: Path(args.output).write_text(payload, encoding='utf-8')
        except OSError as error:
            sys.stdout.write(json.dumps({'schema': SCHEMA, 'operation': args.operation,
                'status': 'invalid', 'metrics': {}, 'error': {'code': 'output_write_failed', 'message': str(error)}},
                allow_nan=False) + '\n')
            return 2
    else: sys.stdout.write(payload)
    return status


if __name__ == '__main__': raise SystemExit(main())
