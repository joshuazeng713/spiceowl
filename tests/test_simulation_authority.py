import tempfile
from pathlib import Path
import unittest
from spiceowl.integrations.authority import load_key, sign, verify

class AuthorityTests(unittest.TestCase):
    def test_signature_binds_caller_method_path_body_and_age(self):
        key=b'x'*32;args=(key,'spiceowl-local','POST','/api/simulation/control',b'{"operation":"submit"}')
        value=sign(*args,timestamp=1000)
        self.assertEqual(verify(*args,value,now=1001),'spiceowl-local')
        for caller,method,path,body in [('native-agent',args[2],args[3],args[4]),(args[1],'GET',args[3],args[4]),(args[1],args[2],'/api/simulation/runtime',args[4]),(args[1],args[2],args[3],b'{}')]:
            with self.assertRaises(PermissionError):verify(key,caller,method,path,body,value,now=1001)
        with self.assertRaises(PermissionError):verify(*args,value,now=1121)
        with self.assertRaises(PermissionError):verify(*args,'bad',now=1001)
    def test_key_must_be_private_regular_bounded_file(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'key';path.write_bytes(b'x'*32);path.chmod(0o600)
            self.assertEqual(load_key(path),b'x'*32)
            path.chmod(0o644)
            with self.assertRaises(PermissionError):load_key(path)
            path.chmod(0o600);path.write_bytes(b'x')
            with self.assertRaises(PermissionError):load_key(path)
            link=Path(d)/'link';link.symlink_to(path)
            with self.assertRaises(PermissionError):load_key(link)
