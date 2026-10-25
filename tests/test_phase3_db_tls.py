import contextlib,importlib.util,io,json,pathlib,subprocess,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('db_tls_test',ROOT/'scripts/phase3-db-tls.py');repair=importlib.util.module_from_spec(s);s.loader.exec_module(repair)
step=repair.step
class DatabaseTLS(unittest.TestCase):
 def folder(self,root):
  config=root/'private/phase2/kolla';config.mkdir(parents=True);certs=config/'certificates';certs.mkdir();step.make_certificates(certs)
  (config/step.MARKER).write_text(json.dumps(step.MARK));(config/'globals.yml').write_text(step.yaml_text(step.settings(config)))
  for p in (config/step.MARKER,config/'globals.yml'):p.chmod(0o600)
  return config,certs
 def test_prepare_missing_files_uses_existing_identity_and_preserves_all_originals(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);config,certs=self.folder(root);fingerprint=step.verify_certificates(certs)
   originals={p:p.read_bytes() for p in config.rglob('*') if p.is_file()};lines=[]
   with patch.object(repair,'REPO',root),patch.object(step,'make_certificates') as generate,patch.object(step,'fill_passwords') as passwords:
    repair.prepare_existing(lines.append,fingerprint);repair.prepare_existing(lines.append,fingerprint)
    generate.assert_not_called();passwords.assert_not_called()
   self.assertEqual(originals,{p:p.read_bytes() for p in originals});self.assertIn('RESULT: PROXYSQL_TLS_READY',lines)
   for target,source in step.PROXYSQL_FILES.items():
    self.assertEqual((certs/target).read_bytes(),(certs/source).read_bytes());self.assertEqual((certs/target).stat().st_mode&0o777,0o600)
   self.assertNotIn('BEGIN PRIVATE KEY','\n'.join(lines))
   result=subprocess.run(['openssl','verify','-CAfile',str(certs/'proxysql-ca.pem'),'-verify_ip',step.VIP,str(certs/'proxysql-cert.pem')],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
 def test_conflicting_destination_stops_before_creating_other_files(self):
  with tempfile.TemporaryDirectory() as d:
   _,certs=self.folder(pathlib.Path(d));(certs/'proxysql-key.pem').write_text('keep unrelated key')
   with self.assertRaises(RuntimeError):step.ensure_proxysql_certificates(certs)
   self.assertFalse((certs/'proxysql-ca.pem').exists());self.assertFalse((certs/'proxysql-cert.pem').exists());self.assertEqual((certs/'proxysql-key.pem').read_text(),'keep unrelated key')
 def test_symlink_destination_refused(self):
  with tempfile.TemporaryDirectory() as d:
   _,certs=self.folder(pathlib.Path(d));(certs/'proxysql-ca.pem').symlink_to(certs/'ca/lab-root.crt')
   with self.assertRaises(RuntimeError):step.ensure_proxysql_certificates(certs)
 def test_wrong_fingerprint_creates_no_aliases(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);_,certs=self.folder(root)
   with patch.object(repair,'REPO',root),self.assertRaises(RuntimeError):repair.prepare_existing(lambda s:None,'0'*64)
   self.assertFalse((certs/'proxysql-ca.pem').exists())
 def test_incomplete_or_replaced_files_caught_by_local_verification(self):
  with tempfile.TemporaryDirectory() as d:
   _,certs=self.folder(pathlib.Path(d))
   with self.assertRaises(RuntimeError):step.verify_proxysql_certificates(certs)
   step.ensure_proxysql_certificates(certs);step.verify_proxysql_certificates(certs)
   (certs/'proxysql-cert.pem').write_text('replaced')
   with self.assertRaises(RuntimeError):step.verify_proxysql_certificates(certs)
 def test_plan_has_no_writes_or_generation(self):
  with patch.object(repair,'prepare_existing') as prepare,contextlib.redirect_stdout(io.StringIO()):self.assertEqual(repair.main([]),0);prepare.assert_not_called()
if __name__=='__main__':unittest.main()
