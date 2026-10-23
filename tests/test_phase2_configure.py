"""Configuration privacy, preservation and actual certificate checks."""
import contextlib,importlib.util,io,json,os,pathlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch,MagicMock
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('configure_step',ROOT/'scripts/phase2-configure.py')
step=importlib.util.module_from_spec(spec);spec.loader.exec_module(step)

class ConfigurationTests(unittest.TestCase):
 def test_settings_match_agreed_scope(self):
  x=step.settings(pathlib.Path('/example/kolla'))
  self.assertTrue(x['enable_cinder']);self.assertTrue(x['enable_cinder_backend_lvm'])
  self.assertFalse(x['enable_heat']);self.assertFalse(x['enable_cinder_backup'])
  self.assertTrue(x['kolla_enable_tls_internal']);self.assertTrue(x['kolla_enable_tls_external'])
  self.assertEqual(x['cinder_cluster_name'],'');self.assertTrue(x['cinder_cluster_skip_precheck'])
  self.assertEqual(x['kolla_internal_vip_address'],'192.168.0.151')
 def test_unknown_config_and_conflicting_host_file_are_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   folder=pathlib.Path(d)/'kolla';folder.mkdir();(folder/'globals.yml').write_text('existing')
   with self.assertRaises(RuntimeError):step.inspect_destination(folder,{})
   self.assertEqual((folder/'globals.yml').read_text(),'existing')
   (folder/step.MARKER).write_text(json.dumps(step.MARK))
   with self.assertRaises(RuntimeError):step.inspect_destination(folder,{folder/'globals.yml':'different'})
   self.assertEqual((folder/'globals.yml').read_text(),'existing')
 def test_default_plan_writes_nothing_and_does_not_generate_credentials(self):
  with patch.object(step,'load_step',return_value=MagicMock()),patch.object(step.tools,'check_paths'),patch.object(step,'prepare') as prepare,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(step.main([]),0);prepare.assert_not_called()
 def test_real_certificate_chain_san_and_file_permissions(self):
  with tempfile.TemporaryDirectory() as d:
   folder=pathlib.Path(d);step.make_certificates(folder)
   fingerprint=step.verify_certificates(folder);self.assertEqual(len(fingerprint),64)
   for p in folder.rglob('*'):
    if p.is_file():self.assertEqual(p.stat().st_mode&0o777,0o600)
   result=subprocess.run(['openssl','verify','-CAfile',str(folder/'ca/lab-root.crt'),'-verify_ip',step.VIP,str(folder/'server.crt')],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
   with patch.object(step,'VIP','192.168.0.199'):
    with self.assertRaises(RuntimeError):step.verify_certificates(folder)
   (folder/'haproxy.pem').write_text('altered')
   with self.assertRaises(RuntimeError):step.verify_certificates(folder)
 def test_existing_credentials_are_not_regenerated_and_values_are_not_logged(self):
  import yaml
  with tempfile.TemporaryDirectory() as d:
   env=pathlib.Path(d)/'venv';template=env/'share/kolla-ansible/etc_examples/kolla/passwords.yml';template.parent.mkdir(parents=True)
   template.write_text('keystone_admin_password: null\ndocker_registry_password: null\n')
   config=pathlib.Path(d)/'kolla';config.mkdir();lines=[]
   secret='ExampleSecretKeptPrivate'
   def generate(args,**kwargs):
    (config/'passwords.yml').write_text(yaml.safe_dump({'keystone_admin_password':secret,'docker_registry_password':None}))
    return subprocess.CompletedProcess(args,0,'','')
   with patch.object(step,'ENV',env),patch.object(step.subprocess,'run',side_effect=generate) as run:
    step.fill_passwords(config,{},lines.append);step.fill_passwords(config,{},lines.append)
    self.assertEqual(run.call_count,1)
   self.assertNotIn(secret,'\n'.join(lines));self.assertEqual((config/'passwords.yml').stat().st_mode&0o777,0o600)
 def test_library_exception_contents_are_not_in_report(self):
  with tempfile.TemporaryDirectory() as d:
   secret='DO_NOT_PRINT_THIS_SECRET'
   old_mask=os.umask(0o077)
   try:
    with patch.object(step,'REPO',pathlib.Path(d)),patch.object(step,'ENV',pathlib.Path(sys.prefix)),patch.object(step,'load_step',return_value=MagicMock()),patch.object(step.tools,'check_paths'),patch('importlib.metadata.version',side_effect=lambda n:'23.0.0' if n=='kolla-ansible' else '2.21.5'),patch.object(step,'prepare',side_effect=ValueError(secret)),contextlib.redirect_stdout(io.StringIO()) as stdout:
     self.assertEqual(step.main(['--apply']),1)
    report,=list((pathlib.Path(d)/'reports').glob('*.txt'))
    self.assertNotIn(secret,report.read_text());self.assertNotIn(secret,stdout.getvalue())
    self.assertIn('LAB_CONFIG_NEEDS_REVIEW',report.read_text());self.assertEqual(report.stat().st_mode&0o777,0o600)
   finally:os.umask(old_mask)

if __name__=='__main__':unittest.main()
