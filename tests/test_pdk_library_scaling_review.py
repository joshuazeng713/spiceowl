"""Independent adversarial review of bounded shared-contract library receipts.

All model text is authored synthetic data. Generated scanner programs execute
only against fresh local temporary directories; these tests never use SSH/PDKs.
Small budget instrumentation exercises the production limiting branches without
constructing a slow 65k-binding or 16-MiB scanner case in every test run.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding import pdk_discovery as discovery
from spiceowl.onboarding.pdk_lut import PdkLut


IDENTITY = {'hostname':'synthetic-scaling-review','username':'fixture-user','uid':1234}
STORAGE = 'shared-model-contracts.v1'
BINDING_GUARD = "sum(len(c['models']) for c in entry_candidates)+len(covered)>65536"
SERIALIZATION_LIMIT = '16744448'


def rehash(receipt):
    value={key:item for key,item in receipt.items() if key!='library_id'}
    receipt['library_id']=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    return receipt


def scan(root, rewrite=None):
    script=discovery.build_pdk_library_script(roots=[str(root)])
    if rewrite is not None:script=rewrite(script)
    output=io.StringIO()
    with contextlib.redirect_stdout(output):exec(script,{})
    return discovery.validate_pdk_library(json.loads(output.getvalue()))


def models(receipt, candidate):
    return discovery.library_candidate_models(receipt,candidate)


def requested(names):
    return {'schema':'spiceowl.netlist-model-requirements.v1','mode':'agent-tb',
        'source_sha256':'b'*64,'required_masters':list(names),'instances':[]}


def common_library(root, *, device_count, corners, unique_tail=False):
    root.mkdir()
    names=['synthetic_%03d'%number for number in range(device_count)]
    (root/'a_definitions.scs').write_text('section device_models\n'+''.join(
        'model %s bsim4 type=n\n'%name for name in names)+'endsection device_models\n')
    # This filename sorts after the definitions, but these include entries are
    # the useful entrypoints and must survive candidate/binding/byte pressure.
    (root/'z_usage.scs').write_text(''.join(
        'section Corner%02d\ninclude "a_definitions.scs" section=device_models\nendsection Corner%02d\n'%(n,n)
        for n in range(corners)))
    if unique_tail:(root/'zz_tail.scs').write_text('model tail_only bsim4 type=p\n')
    return names


def scoped_library(root):
    root.mkdir()
    def wrapper(section, family, ports='d g s b', extra=''):
        return ('section '+section+'\nmodel local_primitive '+family+'\nsubckt shared_device '+ports+
            '\nM0 (d g s b) local_primitive\nends shared_device\n'+extra+'endsection '+section+'\n')
    text=wrapper('N','bsim4 type=n')+wrapper('P','bsim4 type=p',ports='b s g d')
    text+=wrapper('Unknown','unsupported_family')
    text+=wrapper('Unresolved','bsim4 type=n',extra='include "absent.scs" section=not_observed\n')
    for section, first, second in [('NBin','nmos','nmos'),('PBin','pmos','pmos'),('ConflictBin','nmos','pmos')]:
        text+=('section '+section+'\nsimulator lang=spice\n.subckt shared_binned d g s b\n'
            '.model local_bin.1 '+first+' lmin=0 lmax=1u\n.model local_bin.2 '+second+' lmin=1u lmax=2u\n'
            'M0 d g s b local_bin\n.ends shared_binned\nsimulator lang=spectre\nendsection '+section+'\n')
    (root/'scoped.scs').write_text(text)


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK library scanner against local temp dirs")
class LibraryScopeIsolationReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name).resolve()/'library';scoped_library(cls.root)
        cls.library=scan(cls.root)

    def scoped_model(self, section, name):
        candidate=next(row for row in self.library['entry_candidates'] if row['section_candidates']==[section])
        model=next(row for row in models(self.library,candidate) if row['name']==name)
        return candidate,model

    def test_same_model_name_keeps_section_polarity_and_terminal_permutation(self):
        _,n=self.scoped_model('N','shared_device');_,p=self.scoped_model('P','shared_device')
        self.assertEqual((n['polarity'],p['polarity']),('n','p'))
        self.assertEqual(n['ordered_terminal_roles'],['d','g','s','b'])
        self.assertEqual(p['ordered_terminal_roles'],['b','s','g','d'])
        self.assertEqual(n['confidence'],'verified');self.assertEqual(p['confidence'],'verified')
        self.assertNotEqual(n['evidence'],p['evidence'])

    def test_unknown_and_unresolved_contracts_cannot_borrow_a_verified_sibling(self):
        _,unknown=self.scoped_model('Unknown','shared_device')
        candidate,unresolved=self.scoped_model('Unresolved','shared_device')
        self.assertIsNone(unknown['polarity']);self.assertEqual(unknown['confidence'],'unknown')
        self.assertFalse(unknown['auto_prefill_allowed']);self.assertTrue(unknown['needs_confirmation'])
        self.assertFalse(candidate['coverage_complete']);self.assertTrue(candidate['unknown_codes'])
        self.assertNotEqual(unresolved['confidence'],'verified')
        self.assertFalse(unresolved['auto_prefill_allowed']);self.assertTrue(unresolved['needs_confirmation'])

    def test_same_local_bin_names_keep_wrapper_owner_and_conflicting_section(self):
        _,n=self.scoped_model('NBin','shared_binned');_,p=self.scoped_model('PBin','shared_binned')
        _,bad=self.scoped_model('ConflictBin','shared_binned')
        self.assertEqual((n['polarity'],p['polarity']),('n','p'))
        self.assertEqual(n['confidence'],'verified');self.assertEqual(p['confidence'],'verified')
        self.assertIsNone(bad['polarity']);self.assertFalse(bad['auto_prefill_allowed'])
        self.assertEqual(bad['reason'],'conflicting_or_unknown_model_bins')
        self.assertNotEqual(n['evidence'],p['evidence'])

    def test_storage_deduplication_is_by_complete_contract_not_model_name(self):
        self.assertEqual(self.library['contract_storage'],STORAGE)
        refs=[]
        for section in ('N','P','Unknown','Unresolved'):
            candidate,_=self.scoped_model(section,'shared_device')
            refs.append(next(row['contract_id'] for row in candidate['models'] if row['name']=='shared_device'))
        self.assertEqual(len(set(refs)),4)
        for key in refs:self.assertEqual(self.library['model_contracts'][key]['name'],'shared_device')


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK library scanner against local temp dirs")
class SharedContractIntegrityReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.base=Path(cls.temp.name).resolve();cls.root=cls.base/'library'
        common_library(cls.root,device_count=3,corners=3)
        cls.library=scan(cls.root)

    def checked_rejection(self, mutation):
        receipt=copy.deepcopy(self.library);mutation(receipt);rehash(receipt)
        with self.assertRaises(DomainError):discovery.validate_pdk_library(receipt)
        with PdkLut(self.base/'rejections.sqlite3') as lut:
            with self.assertRaises(DomainError):lut.put_library(receipt,server_identity=IDENTITY,observed_at=10)

    def test_rehashed_missing_table_record_and_wrong_inner_hash_are_rejected(self):
        def missing(r):del r['model_contracts'][r['entry_candidates'][0]['models'][0]['contract_id']]
        def changed(r):
            key=r['entry_candidates'][0]['models'][0]['contract_id'];r['model_contracts'][key]['polarity']='p'
        for mutation in (missing,changed):
            with self.subTest(mutation=mutation.__name__):self.checked_rejection(mutation)

    def test_rehashed_wrong_ref_name_is_rejected_even_with_matching_coverage(self):
        def wrong_name(r):
            candidate=r['entry_candidates'][0];candidate['models'][0]['name']='renamed_model'
            candidate['master_coverage']=sorted(row['name'] for row in candidate['models'])
            names=sorted({row['name'] for entry in r['entry_candidates'] for row in entry['models']})
            r['coverage']['indexed_model_names']=names;r['coverage']['indexed_model_count']=len(names)
        self.checked_rejection(wrong_name)

    def test_rehashed_unused_table_entries_and_noncanonical_keys_are_rejected(self):
        def unused(r):
            extra=copy.deepcopy(next(iter(r['model_contracts'].values())));extra['name']='unused_contract'
            r['model_contracts'][discovery._digest(extra)]=extra
        def bad_key(r):
            old=next(iter(r['model_contracts']));r['model_contracts']['0'*64]=r['model_contracts'].pop(old)
            for entry in r['entry_candidates']:
                for ref in entry['models']:
                    if ref['contract_id']==old:ref['contract_id']='0'*64
        for mutation in (unused,bad_key):
            with self.subTest(mutation=mutation.__name__):self.checked_rejection(mutation)

    def test_rehashed_mixed_inline_missing_id_extra_fields_and_duplicate_refs_rejected(self):
        def inline(r):
            entry=r['entry_candidates'][0];entry['models'][0]=copy.deepcopy(r['model_contracts'][entry['models'][0]['contract_id']])
        def absent_id(r):r['entry_candidates'][0]['models'][0].pop('contract_id')
        def override(r):r['entry_candidates'][0]['models'][0]['polarity']='p'
        def duplicate(r):r['entry_candidates'][0]['models'].append(copy.deepcopy(r['entry_candidates'][0]['models'][0]))
        for mutation in (inline,absent_id,override,duplicate):
            with self.subTest(mutation=mutation.__name__):self.checked_rejection(mutation)

    def test_legacy_inline_receipt_remains_valid_and_queries_return_full_models(self):
        receipt=copy.deepcopy(self.library)
        for entry in receipt['entry_candidates']:entry['models']=copy.deepcopy(models(self.library,entry))
        receipt.pop('contract_storage');receipt.pop('model_contracts');rehash(receipt)
        self.assertIs(discovery.validate_pdk_library(receipt),receipt)
        with PdkLut(self.base/'inline.sqlite3') as lut:
            lut.put_library(receipt,server_identity=IDENTITY,observed_at=10)
            result=lut.lookup_library_models(server_identity=IDENTITY,roots=[str(self.root)],now=11,
                required_masters=['synthetic_002'],requirements=requested(['synthetic_002']))
        self.assertEqual(result['status'],'hit')
        for entry in result['discovery']['entry_candidates']:
            self.assertNotIn('contract_id',entry['models'][0]);self.assertEqual(entry['models'][0]['polarity'],'n')

    def test_candidate_and_model_order_do_not_change_candidate_identity_or_contents(self):
        before={entry['candidate_id']:{row['name']:row for row in models(self.library,entry)}
                for entry in self.library['entry_candidates']}
        changed=copy.deepcopy(self.library);changed['entry_candidates'].reverse()
        for entry in changed['entry_candidates']:entry['models'].reverse()
        rehash(changed);discovery.validate_pdk_library(changed)
        after={entry['candidate_id']:{row['name']:row for row in models(changed,entry)}
               for entry in changed['entry_candidates']}
        self.assertEqual(before,after)


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK library scanner against local temp dirs")
class LibraryScalingBudgetReview(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve()

    def test_actual_stdout_is_compact_and_validator_bounds_real_encoded_bytes(self):
        root=self.base/'compact';common_library(root,device_count=3,corners=3)
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(discovery.build_pdk_library_script(roots=[str(root)]),{})
        receipt=json.loads(output.getvalue())
        compact=json.dumps(receipt,sort_keys=True,separators=(',',':'))
        self.assertEqual(output.getvalue(),compact+'\n')
        self.assertLess(len(compact.encode()),len(json.dumps(receipt,sort_keys=True).encode()))
        self.assertLessEqual(len(output.getvalue().encode()),discovery.MAX_LIBRARY_RESULT_BYTES)
        inline=copy.deepcopy(receipt)
        for entry in inline['entry_candidates']:
            entry['models']=copy.deepcopy(models(receipt,entry))
        inline.pop('contract_storage');inline.pop('model_contracts');rehash(inline)
        for encoding,value in [('shared',receipt),('inline',inline)]:
            with self.subTest(encoding=encoding):
                size=len(json.dumps(value,separators=(',',':')).encode())
                with patch.object(discovery,'MAX_LIBRARY_RESULT_BYTES',size):
                    self.assertIs(discovery.validate_pdk_library(value),value)
                with patch.object(discovery,'MAX_LIBRARY_RESULT_BYTES',size-1):
                    with self.assertRaises(DomainError):discovery.validate_pdk_library(value)

    def test_48_corners_by_200_models_index_completely_and_query_arbitrary_subsets(self):
        root=self.base/'scaling';names=common_library(root,device_count=200,corners=48)
        receipt=scan(root)
        self.assertEqual(receipt['contract_storage'],STORAGE)
        self.assertEqual(receipt['status'],'complete')
        self.assertTrue(receipt['coverage']['complete_within_policy'])
        self.assertEqual(receipt['coverage']['indexed_model_names'],names)
        self.assertEqual(len(receipt['entry_candidates']),49)
        self.assertEqual(sum(len(row['models']) for row in receipt['entry_candidates']),9800)
        self.assertEqual(len(receipt['model_contracts']),200)
        self.assertLessEqual(len(json.dumps(receipt,separators=(',',':')).encode()),16*1024*1024)
        selected=next(row for row in receipt['entry_candidates'] if row['entry_role']=='include_entry_candidate')
        original=copy.deepcopy(receipt)
        with PdkLut(self.base/'large.sqlite3') as lut:
            lut.put_library(receipt,server_identity=IDENTITY,observed_at=10)
            with patch('builtins.open',side_effect=AssertionError('Query must not rescan PDK files')):
                for selected_names in ([names[-1]],[names[0],names[57],names[199]]):
                    answer=lut.lookup_library_models(server_identity=IDENTITY,roots=[str(root)],now=11,
                        required_masters=selected_names,requirements=requested(selected_names),
                        library_candidate_id=selected['candidate_id'])
                    self.assertEqual(answer['status'],'hit');self.assertFalse(answer['scan_required'])
                    self.assertFalse(answer['remote_freshness_verified'])
                    self.assertEqual(len(answer['discovery']['entry_candidates']),1)
                    resolved=answer['discovery']['entry_candidates'][0]['models']
                    self.assertEqual({row['name'] for row in resolved},set(selected_names))
                    self.assertTrue(all(row['polarity']=='n' and row['confidence']=='verified' for row in resolved))
                    self.assertTrue(all('contract_id' not in row for row in resolved))
        self.assertEqual(receipt,original)

    def test_binding_cap_keeps_include_entries_before_lexically_earlier_definitions(self):
        root=self.base/'binding-cap';common_library(root,device_count=4,corners=4)
        complete=scan(root)
        def smaller_budget(script):
            self.assertEqual(script.count(BINDING_GUARD),1)
            return script.replace(BINDING_GUARD,BINDING_GUARD.replace('>65536','>8'),1)
        limited=scan(root,smaller_budget)
        self.assertEqual(limited['status'],'partial');self.assertFalse(limited['coverage']['complete_within_policy'])
        self.assertIn('library_binding_limit',{row['code'] for row in limited['unknown']})
        self.assertEqual(len(limited['entry_candidates']),2)
        by_id={row['candidate_id']:row for row in complete['entry_candidates']}
        for row in limited['entry_candidates']:
            self.assertEqual(row['entry_role'],'include_entry_candidate')
            self.assertEqual(row,by_id[row['candidate_id']])
            self.assertEqual(models(limited,row),models(complete,by_id[row['candidate_id']]))

    def test_byte_cap_remains_partial_and_prunes_unused_shared_contracts(self):
        root=self.base/'byte-cap';common_library(root,device_count=20,corners=12,unique_tail=True)
        complete=scan(root);budget=max(16384,len(json.dumps(complete,sort_keys=True,separators=(',',':')).encode())//2)
        def smaller_budget(script):
            self.assertGreaterEqual(discovery._LIBRARY_SERIALIZER.count(SERIALIZATION_LIMIT),3)
            self.assertEqual(script.count(SERIALIZATION_LIMIT),discovery._LIBRARY_SERIALIZER.count(SERIALIZATION_LIMIT))
            return script.replace(SERIALIZATION_LIMIT,str(budget))
        limited=scan(root,smaller_budget)
        self.assertEqual(limited['status'],'partial');self.assertFalse(limited['coverage']['complete_within_policy'])
        self.assertLess(len(limited['entry_candidates']),len(complete['entry_candidates']))
        self.assertGreater(limited['coverage']['omitted_entry_count'],0)
        refs={row['contract_id'] for entry in limited['entry_candidates'] for row in entry['models']}
        self.assertEqual(refs,set(limited['model_contracts']))
        self.assertTrue(all(row['entry_role']=='include_entry_candidate' for row in limited['entry_candidates']))
        by_id={row['candidate_id']:row for row in complete['entry_candidates']}
        for row in limited['entry_candidates']:
            self.assertEqual(row,by_id[row['candidate_id']])
            self.assertEqual(models(limited,row),models(complete,by_id[row['candidate_id']]))


if __name__=='__main__':unittest.main()
