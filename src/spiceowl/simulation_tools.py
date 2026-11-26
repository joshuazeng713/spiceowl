"""Transport-neutral, bounded simulation tools. Active operations belong to hosts.

An embedding host supplies explicit readers and finite operation callbacks. It
must enforce policy(name, arguments) before dispatch. The standalone Codex bridge
does so with a frozen human-review card. No model argument grants authorization.
"""
from copy import deepcopy
import json
import re

from spiceowl.domain.common import DomainError
from spiceowl.embedded import dispatch_read_tool, read_tool_definitions

MAX_ARGUMENT_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024


def _object(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


_TEXT = {'type': 'string', 'maxLength': 1024 * 1024}
_ID = {'type': 'string', 'minLength': 1, 'maxLength': 180, 'pattern': r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}$'}
_SHA = {'type': 'string', 'pattern': r'^[a-f0-9]{40}$'}
_JSON = {'type': 'object'}
_SUPPORT = {'type': 'object', 'maxProperties': 64, 'additionalProperties': _TEXT}
_HOSTS = {'type': 'array', 'minItems': 1, 'maxItems': 32, 'uniqueItems': True, 'items': _ID}
_OPS = ['spec_analyze', 'coherent_fft', 'sine_fit3', 'sine_fit4', 'weight_calibrate',
        'reconstruct_codes', 'histogram_linearity', 'tran_metrics', 'ac_metrics', 'dc_op_metrics']


def _definition(name, description, schema, effect='read'):
    return {'name': name, 'description': description, 'inputSchema': schema,
            'annotations': {'readOnlyHint': effect == 'read', 'destructiveHint': False,
                'idempotentHint': effect == 'read', 'openWorldHint': effect != 'read'}}


_PURE = [
    _definition('spiceowl_inspect_agent_source', 'Inspect supplied Spectre/Verilog-A source bytes and explicit helpers. Never reads a path or runs a solver.',
        _object({'source': _TEXT, 'source_id': {'type': 'string', 'maxLength': 512}, 'support_files': _SUPPORT}, ('source',))),
    _definition('spiceowl_netlist_requirements', 'Extract exact unresolved model requirements; never infer a private device polarity, corner or legal dimensions.',
        _object({'source': _TEXT, 'mode': {'type': 'string', 'enum': ['model-discovery', 'agent-tb']}, 'support_files': _SUPPORT}, ('source',))),
    _definition('spiceowl_validate_agent_tb', 'Statically validate authored DUT/TB, dependencies and complete criteria. static_ready grants no simulation authority.',
        _object({'source': _TEXT, 'testbench': _TEXT, 'criteria': _JSON, 'pdk_binding': _JSON, 'support_files': _SUPPORT}, ('source', 'testbench', 'criteria', 'pdk_binding'))),
    _definition('spiceowl_prepare_experiment', 'Compile explicit committed source text and a registered recipe into a frozen bundle. Does not stage or launch.',
        _object({'sources': _SUPPORT, 'selection': _JSON, 'spec': _JSON, 'model_profile': _JSON}, ('sources', 'selection', 'spec', 'model_profile'))),
    _definition('spiceowl_pdk_scope_catalog', 'Page entry/section IDs in an already supplied PDK metadata inventory; no filesystem or network read.',
        _object({'library': _JSON, 'entry_id': {'type': ['string', 'null']}, 'offset': {'type': 'integer', 'minimum': 0},
                 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 64}}, ('library',))),
    _definition('spiceowl_project_pdk_scopes', 'Project explicitly selected same-entry sections into a frozen dependency proposal; never selects a corner or runs an audit.',
        _object({'library': _JSON, 'requirements': _JSON, 'server_identity': _JSON, 'entry_id': {'type': 'string'},
                 'section_ids': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': {'type': 'string'}},
                 'tmi_option_id': {'type': ['string', 'null']}}, ('library', 'requirements', 'server_identity', 'entry_id', 'section_ids'))),
    _definition('spiceowl_verify_scope_audit', 'Compare an explicit selected scope with its audit receipt. Comparison alone does not prove current remote freshness or authorize execution.',
        _object({'selection': _JSON, 'audit': _JSON, 'audit_config': _JSON}, ('selection', 'audit', 'audit_config'))),
    _definition('spiceowl_diagnose_spectre', 'Classify supplied bounded log evidence. Connection loss is not job failure; suggestions cannot change settings.',
        _object({'log_text': _TEXT, 'execution_state': {'type': 'string', 'enum': ['queued', 'running', 'completed', 'failed', 'cancelled', 'unknown']},
                 'exit_code': {'type': ['integer', 'null'], 'minimum': -255, 'maximum': 255},
                 'acceptance': {'type': ['object', 'null']}, 'oom_evidence': {'type': ['object', 'null']}}, ('log_text', 'execution_state', 'exit_code'))),
    _definition('spiceowl_parse_psf', 'Parse supplied bounded native PSF ASCII. Does not read server files or infer scientific validity from a parse.',
        _object({'text': _TEXT, 'analysis_kind': {'type': 'string', 'enum': ['tran', 'ac', 'dc', 'dc_op']},
                 'analysis_name': {'type': ['string', 'null']}}, ('text', 'analysis_kind'))),
    _definition('spiceowl_adctools', 'Compute a named deterministic metric from explicit arrays and backend parameters. Does not extract clock-phase samples or run arbitrary postprocessing code.',
        _object({'operation': {'type': 'string', 'enum': _OPS}, 'arguments': _JSON}, ('operation', 'arguments'))),
    _definition('spiceowl_next_action', 'Evaluate an explicit experiment/run snapshot and return bounded missing fields, permitted next operations, and forbidden actions without network or filesystem I/O.',
        _object({'state': _JSON}, ('state',))),
]

# Only these host-bound operations can be attached. Their handlers retain the
# execution owner's authentication, target validation and review ledger.
_OWNER = [
    _definition('spiceowl_probe_host', 'Actively check one registered SSH host. Pauses for manual human approval; do not call in a loop across hosts or when [SPICEOWL_CTX] / spiceowl_list_hosts already provides host status unless the user explicitly asks to probe that host. No simulator launch.', _object({'host_id': _ID}, ('host_id',)), 'approval'),
    _definition('spiceowl_scan_resources', 'Actively sample an explicit registered host set once. Pauses for manual human approval; do not call when [SPICEOWL_CTX] already supplies recent live host metrics and SPICE licenses unless the user explicitly requests a fresh SSH scan. Observations are not reservations.', _object({'host_ids': _HOSTS}, ('host_ids',)), 'approval'),
    _definition('spiceowl_recommend_hosts', 'Evaluate explicit CPU/RAM requirements against stored resource observations; does not contact a host.', _object({'host_ids': _HOSTS, 'requirements': _JSON}, ('host_ids', 'requirements'))),
    _definition('spiceowl_pdk_lookup', 'Query an operator-selected existing PDK LUT with explicit observation scope. A cache hit never replaces a fresh audit.',
        _object({'operation': {'type': 'string', 'enum': ['lookup', 'query_bindings', 'query_best_bindings', 'query_library_devices', 'lookup_library_models']}, 'arguments': _JSON}, ('operation', 'arguments'))),
    _definition('spiceowl_onboarding', 'Call the configured execution owner for one finite onboarding operation. Writes/remote checks require human review; Service review and submission remain separate.',
        _object({'operation': {'type': 'string'}, 'arguments': _JSON}, ('operation', 'arguments')), 'approval'),
    _definition('spiceowl_runtime', 'Read the configured execution owner runtime snapshot; never creates a second Runner.', _object()),
    _definition('spiceowl_live_control', 'Request one configured Runner action after human tool review. Preserve original identity after uncertainty; never retries automatically.',
        _object({'operation': {'type': 'string', 'enum': ['scan', 'prepare', 'run', 'inspect', 'submit', 'status', 'collect', 'watch', 'unwatch']},
                 'arguments': _JSON}, ('operation', 'arguments')), 'approval'),
    _definition('spiceowl_lookup_submission', 'Reconcile one original submission key through the configured owner. Lookup never authorizes replay.',
        _object({'key': {'type': 'string', 'minLength': 8, 'maxLength': 160}}, ('key',))),
    _definition('spiceowl_direct_read', 'Read locally stored direct-execution runs, PVT/MC batch summaries, submission key mappings, collected five-dimensional reports, lossless analog metrics, recovery reports, incremental log tails, overnight watch handoff events, or local binary/ASCII waveform slices & cycle metrics without network I/O.',
        _object({'operation': {'type': 'string', 'enum': ['list', 'inspect', 'lookup', 'report', 'waveform_index', 'waveform_query', 'waveform_cycles', 'waveform_measure', 'recovery_reports', 'log_tail', 'watch_events', 'watch_policy', 'batch_list', 'batch_inspect', 'batch_report']},
                 'arguments': _JSON}, ('operation', 'arguments'))),
    _definition('spiceowl_direct_control', 'Run one bounded direct SSH/LSF/Slurm run, PVT/MC batch operation, or record an Agent watch-handoff decision. Respects the explicit OvernightAutonomyPolicy when enabled; otherwise requires human tool review.',
        _object({'operation': {'type': 'string', 'enum': ['prepare', 'stage', 'launch', 'status', 'cancel', 'collect', 'watch_record_decision', 'batch_prepare', 'batch_dispatch', 'batch_refresh', 'batch_cancel']},
                 'arguments': _JSON}, ('operation', 'arguments')), 'approval'),
    _definition('spiceowl_library_browse', 'Read-only browse or search remote SSH directories, Cadence cds.lib libraries, Virtuoso schematic/maestro views, and circuit netlists on a registered host. Runs immediately without human approval. Use operation="find" with query (e.g. "2026_pipeline research4 tb_amp") to locate libraries/schematics quickly.',
        _object({
            'host_id': _ID,
            'operation': {'type': 'string', 'enum': ['browse', 'find']},
            'path': {'type': 'string', 'maxLength': 2048},
            'query': {'type': 'string', 'minLength': 1, 'maxLength': 512},
            'max_depth': {'type': 'integer', 'minimum': 1, 'maximum': 6},
        }), 'read'),
    _definition('spiceowl_library_open', 'Read-only open a remote netlist or export a Virtuoso OA schematic (with Maestro simulation setup and RNL 0.3 geometry) directly into the user webpage editor and Library Manager state. Never modifies remote design files and never launches a simulation; runs immediately without human approval.',
        _object({
            'host_id': _ID,
            'mode': {'type': 'string', 'enum': ['schematic', 'netlist']},
            'remote_path': {'type': 'string', 'minLength': 1, 'maxLength': 2048},
            'include_maestro': {'type': 'boolean'},
            'maestro_view': {'type': 'string', 'maxLength': 120},
            'preserve_rnl': {'type': 'boolean'},
            'cds_lib': {'type': 'string', 'maxLength': 2048},
        }, ('mode', 'remote_path')), 'read'),
    _definition('spiceowl_ui_configure', 'Update any option on the active SpiceOwl webpage immediately (target server, simulator, backend, CPUs, memory, wall time, output format, run name, netlist text or appended statements, include dirs, remote env, LSF/Slurm scheduler, overnight recovery policy, or active page/tab: setup, runs, recovery). Only updates UI drafts and view options; never launches a simulation; runs immediately without human approval.',
        _object({
            'page': {'type': 'string', 'enum': ['quickstart', 'hosts', 'direct', 'overview', 'agents']},
            'direct_tab': {'type': 'string', 'enum': ['setup', 'runs', 'recovery', 'watch', 'monitor', 'waveforms', 'batch']},
            'host_id': _ID,
            'target_mode': {'type': 'string', 'enum': ['host', 'pool']},
            'simulator': {'type': 'string', 'enum': ['spectre', 'ngspice']},
            'backend': {'type': 'string', 'enum': ['ssh_direct', 'lsf', 'slurm']},
            'run_key': {'type': 'string', 'maxLength': 128},
            'cpus': {'type': 'integer', 'minimum': 1, 'maximum': 4096},
            'memory_gib': {'type': 'number', 'minimum': 0.001, 'maximum': 1048576},
            'wall_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 604800},
            'output_format': {'type': 'string', 'enum': ['psfbin', 'psfascii', 'raw']},
            'recheck_resources': {'type': 'boolean'},
            'allow_collect': {'type': 'boolean'},
            'netlist_text': _TEXT,
            'append_netlist': _TEXT,
            'include_dirs': {'type': 'array', 'maxItems': 64, 'items': {'type': 'string', 'maxLength': 1024}},
            'env_modules': {'type': 'array', 'maxItems': 32, 'items': {'type': 'string', 'maxLength': 128}},
            'env_scripts': {'type': 'array', 'maxItems': 32, 'items': {'type': 'string', 'maxLength': 512}},
            'remote_python': {'type': 'string', 'maxLength': 256},
            'remote_root': {'type': 'string', 'maxLength': 256},
            'scheduler': _JSON,
            'recovery_policy': _JSON,
            'batch': _JSON,
        }), 'read'),
]


def _json_copy(value, limit):
    def bounded(item, depth=0):
        if depth > 32:
            raise ValueError('depth')
        if type(item) is dict:
            if len(item) > 16384 or any(type(key) is not str for key in item): raise ValueError('object')
            for value in item.values(): bounded(value, depth + 1)
        elif type(item) is list:
            if len(item) > 200000: raise ValueError('array')
            for value in item: bounded(value, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool): raise ValueError('type')
    try:
        bounded(value)
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False)
        if len(raw.encode()) > limit: raise ValueError('size')
        return json.loads(raw)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise DomainError('Tool data exceeds its finite JSON limits or is not valid JSON.', 'invalid_tool_data') from None


def _validate(value, schema):
    expected = schema.get('type')
    if isinstance(expected, list):
        matches = any(_type_matches(value, item) for item in expected)
    else:
        matches = expected is None or _type_matches(value, expected)
    if not matches or 'enum' in schema and value not in schema['enum']:
        raise DomainError('Tool argument type or enum does not match its schema.', 'invalid_tool_arguments')
    if type(value) is dict:
        properties = schema.get('properties', {})
        if not set(schema.get('required', ())) <= set(value) or len(value) > schema.get('maxProperties', 16384):
            raise DomainError('Tool argument fields are incomplete or oversized.', 'invalid_tool_arguments')
        for key, item in value.items():
            if key in properties: _validate(item, properties[key])
            elif schema.get('additionalProperties') is False:
                raise DomainError('Unexpected tool argument; model arguments cannot choose paths or approvals.', 'invalid_tool_arguments')
            elif isinstance(schema.get('additionalProperties'), dict): _validate(item, schema['additionalProperties'])
    elif type(value) is list:
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 200000):
            raise DomainError('Tool array length is out of bounds.', 'invalid_tool_arguments')
        if schema.get('uniqueItems') and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise DomainError('Tool array items must be distinct.', 'invalid_tool_arguments')
        for item in value: _validate(item, schema.get('items', {}))
    elif type(value) is str:
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 1024 * 1024) or 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            raise DomainError('Tool string length or format is invalid.', 'invalid_tool_arguments')
    elif type(value) in (int, float):
        if value < schema.get('minimum', float('-inf')) or value > schema.get('maximum', float('inf')):
            raise DomainError('Tool numeric argument is out of bounds.', 'invalid_tool_arguments')


def _type_matches(value, expected):
    return {'object': type(value) is dict, 'array': type(value) is list, 'string': type(value) is str,
            'integer': type(value) is int, 'number': type(value) in (int, float),
            'boolean': type(value) is bool, 'null': value is None}.get(expected, False)


class SimulationToolDispatcher:
    """Pure engine calls plus explicitly injected host capabilities, without IO at init."""
    def __init__(self, *, mode='embedded_in_circuitgit', read_views=None, owner_handlers=None, policy_override=None):
        from spiceowl.agent_channel import normalize_agent_mode
        self.mode = normalize_agent_mode(mode)
        if read_views is not None:
            from spiceowl.read_api import ReadViews
            if not isinstance(read_views, ReadViews):
                raise DomainError('Provide an already opened ReadViews, never a data directory.', 'read_views_unavailable')
        self.read_views = read_views
        self._policy_override = policy_override
        self._handlers = dict(owner_handlers or {})
        allowed = {entry['name'] for entry in _OWNER}
        if set(self._handlers) - allowed or any(not callable(value) for value in self._handlers.values()):
            raise DomainError('Only finite host-owned operations can be bound.', 'invalid_tool_binding')
        definitions = [*_PURE, *(entry for entry in _OWNER if entry['name'] in self._handlers)]
        if read_views is not None: definitions = [*read_tool_definitions(), *definitions]
        self._definitions = {entry['name']: deepcopy(entry) for entry in definitions}
        owner = self._handlers.get('spiceowl_onboarding')
        if owner is not None and hasattr(owner, 'operation_names'):
            self._definitions['spiceowl_onboarding']['inputSchema']['properties']['operation']['enum'] = list(owner.operation_names)
            self._definitions['spiceowl_onboarding']['description'] += ' Call operation=catalog with empty arguments first for exact per-operation schemas.'

    def definitions(self):
        return deepcopy(list(self._definitions.values()))

    def policy(self, name, arguments):
        entry = self._definitions.get(name)
        if entry is None: return 'deny'
        if name == 'spiceowl_onboarding' and isinstance(arguments, dict):
            if arguments.get('operation') in getattr(self._handlers[name], 'read_operations', ()):
                return 'read'
        if callable(self._policy_override):
            override = self._policy_override(name, arguments)
            if override in ('read', 'approval', 'deny'):
                return override
        return 'read' if entry['annotations']['readOnlyHint'] else 'approval'

    def dispatch(self, name, arguments, *, context=None):
        entry = self._definitions.get(name)
        if entry is None:
            raise DomainError('Tool is unknown or not configured by the execution owner.', 'tool_unavailable')
        arguments = _json_copy(arguments, MAX_ARGUMENT_BYTES)
        _validate(arguments, entry['inputSchema'])
        if name in {row['name'] for row in read_tool_definitions()}:
            result = dispatch_read_tool(self.read_views, name, arguments)
        elif name in self._handlers:
            result = self._handlers[name](arguments, context=_json_copy(context or {}, 16384))
        else:
            result = self._pure(name, arguments)
        return _json_copy(result, MAX_RESULT_BYTES)

    @staticmethod
    def _pure(name, args):
        if name == 'spiceowl_inspect_agent_source':
            from spiceowl.onboarding.agent_tb import inspect_agent_source
            return inspect_agent_source(**args)
        if name == 'spiceowl_netlist_requirements':
            from spiceowl.onboarding.pdk_discovery import inspect_netlist
            return inspect_netlist(**args)
        if name == 'spiceowl_validate_agent_tb':
            from spiceowl.onboarding.agent_tb import validate_agent_tb
            return validate_agent_tb(**args)
        if name == 'spiceowl_prepare_experiment':
            from spiceowl.experiments.recipes import prepare_experiment
            # Engine compilation returns byte-preserved files. JSON uses exact
            # UTF-8 text here; its existing manifest still binds original bytes.
            result = prepare_experiment(**(args | {'sources': {key: text.encode('utf-8') for key, text in args['sources'].items()}}))
            def text_files(value):
                if isinstance(value, bytes): return value.decode('utf-8')
                if isinstance(value, dict): return {key: text_files(item) for key, item in value.items()}
                if isinstance(value, list): return [text_files(item) for item in value]
                return value
            return text_files(result)
        if name == 'spiceowl_pdk_scope_catalog':
            from spiceowl.onboarding.pdk_scope import library_scope_catalog
            return library_scope_catalog(**args)
        if name == 'spiceowl_project_pdk_scopes':
            from spiceowl.onboarding.pdk_scope import project_library_scopes
            return project_library_scopes(**args)
        if name == 'spiceowl_verify_scope_audit':
            from spiceowl.onboarding.pdk_scope import verify_scope_selection_audit
            return verify_scope_selection_audit(**args)
        if name == 'spiceowl_diagnose_spectre':
            from spiceowl.integrations.spectre_diagnostics import diagnose_spectre
            return diagnose_spectre(**args)
        if name == 'spiceowl_parse_psf':
            from spiceowl.integrations.spectre_results import parse_psf_ascii, parse_psf_analysis
            if args['analysis_kind'] == 'tran': return parse_psf_ascii(args['text'])
            return parse_psf_analysis(**args)
        if name == 'spiceowl_adctools':
            from spiceowl.adctools import AnalysisError, analyze
            try:
                return analyze(**args)
            except AnalysisError as exc:
                # This pure algorithm's declared validation/dependency errors
                # are correctable inputs, never uncertain remote execution.
                raise DomainError(str(exc), exc.code) from None
        if name == 'spiceowl_next_action':
            from spiceowl.orchestrator import evaluate_next_action
            return evaluate_next_action(args['state'])
        raise DomainError('Unknown engine tool.', 'unknown_tool')
