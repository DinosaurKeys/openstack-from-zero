import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file); m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
reset=load('reset_test','phase3-reset.py'); node=load('reset_node_test','phase3-reset-node.py')
net=load('ha_network_test','phase4-create-networks.py'); deploy=load('ha_deploy_test','phase3-deploy.py')
observe=load('ha_observe_test','phase3-deploy-node.py')


class ResetDiskTests(unittest.TestCase):
    def inputs(self):
        return ({'path':'/dev/sda','type':'disk','tran':'sata','serial':node.TARGETS['node1'][1], 'size':node.SIZE,'mountpoints':[None]},
                [{'pv_name':'/dev/sda','vg_name':node.VG}], [{'vg_name':node.VG,'pv_count':'1'}])
    def guard(self,disk=None,pvs=None,vgs=None,ancestors=None):
        d,p,v=self.inputs();node.guard_disk('node1',disk or d,pvs or p,vgs or v,'/dev/sda',ancestors or ['/dev/nvme0n1'])
    def test_correct_reviewed_disk_accepted(self):self.guard()
    def test_wrong_serial_size_transport_and_system_ancestry_rejected(self):
        for key,value in [('serial','OTHER'),('size',120),('tran','nvme'),('type','part')]:
            disk,_,_=self.inputs();disk[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.guard(disk=disk)
        with self.assertRaisesRegex(RuntimeError,'operating system'):self.guard(ancestors=['/dev/sda'])
    def test_extra_or_foreign_pv_and_mounted_lv_rejected(self):
        with self.assertRaises(RuntimeError):self.guard(pvs=[{'pv_name':'/dev/nvme0n1','vg_name':node.VG}])
        with self.assertRaises(RuntimeError):self.guard(pvs=[{'pv_name':'/dev/sda','vg_name':node.VG},{'pv_name':'/dev/sdb','vg_name':node.VG}])
        disk,_,_=self.inputs();disk['children']=[{'mountpoints':['/data']}]
        with self.assertRaisesRegex(RuntimeError,'mounted'):self.guard(disk=disk)
    def record(self):
        return {'node':'node1','device':'/dev/sda','protected':{'system':'unchanged'},'kolla_containers':[], 'namespaces':[],
                'cinder_lvs':[{'lv_attr':'twi-a-tz--'}], 'vip':False,'vip_cidr':None}
    def test_only_cinder_lvs_removed_and_pv_vg_preserved(self):
        before=self.record();after=copy.deepcopy(before);after['cinder_lvs']=[];calls=[]
        iterator=iter([before,before,after])
        result=node.clean('node1',lambda n:next(iterator),lambda args:calls.append(args))
        self.assertEqual(calls,[['lvremove','--yes','cinder-volumes']]);self.assertEqual(result['protected'],before['protected'])
    def test_live_containers_open_lvs_and_storage_drift_block_cleanup(self):
        for key,value in [('kolla_containers',['mariadb']),('namespaces',['qrouter-x']),('cinder_lvs',[{'lv_attr':'Vwi-aotz--'}])]:
            before=self.record();before[key]=value;calls=[]
            with self.subTest(key=key),self.assertRaises(RuntimeError):node.clean('node1',lambda n:before,lambda a:calls.append(a))
            self.assertEqual(calls,[])
        before=self.record();different=copy.deepcopy(before);different['device']='/dev/nvme0n1';calls=[];it=iter([before,different])
        with self.assertRaisesRegex(RuntimeError,'changed'):node.clean('node1',lambda n:next(it),lambda a:calls.append(a))
        self.assertEqual(calls,[])
    def test_only_observed_vip_prefix_removed(self):
        before=self.record();before.update(vip=True,vip_cidr='192.168.0.151/32',cinder_lvs=[])
        after=copy.deepcopy(before);after.update(vip=False,vip_cidr=None);it=iter([before,after,after]);calls=[]
        node.clean('node1',lambda n:next(it),lambda a:calls.append(a))
        self.assertEqual(calls,[['ip','address','delete','192.168.0.151/32','dev','enp0s31f6']])


class ResetWrapperTests(unittest.TestCase):
    def test_default_plan_no_io_or_prompts(self):
        with patch.object(reset,'execute') as execute,patch.object(reset.getpass,'getpass') as password,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reset.main([]),0);execute.assert_not_called();password.assert_not_called()
    def simulate(self,failure=None):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'reports/run';work.mkdir(parents=True)
            config=root/'private/phase2/kolla';config.mkdir(parents=True)
            (config/'globals.yml').write_text('old globals');(config/'passwords.yml').write_text('PASSWORD-SECRET')
            (config/'clouds.yaml').write_text('CLIENT-SECRET');(config/'certificates').mkdir();(config/'certificates/ca').write_text('CA-BYTES')
            inventory=config.parent/'inventory.ini';inventory.write_text('fixture')
            phase4=root/'private/phase4';phase4.mkdir();(phase4/'networks.json').write_text('{"old":"id"}')
            (phase4/'cache.img').write_text('IMAGE-CACHE')
            for p in [*config.rglob('*'),inventory,*phase4.glob('*')]:
                if p.is_file():p.chmod(0o600)
            calls=[];logs=[];commands=[]
            def factory(log,env):
                class Runner:
                    def run(self,args,terminal=False):
                        a=list(map(str,args));commands.append(a)
                        if '--syntax-check' in a:return
                        n=a[a.index('--limit')+1]
                        if 'destroy' in a:
                            calls.append('destroy:'+n)
                            if failure=='destroy:'+n:raise RuntimeError('fixture destroy stopped')
                        else:
                            stage=Path(a[a.index('-i')+2]).stem;calls.append(stage+':'+n)
                            if failure==stage+':'+n:raise RuntimeError('fixture host stopped')
                            (work/stage/(n+'.json')).write_text(json.dumps({'node':n,'ready':True,'protected':{'os':'unchanged'}}))
                return Runner()
            with patch.object(reset,'REPO',root),patch.object(reset,'source_guard'),patch.object(reset,'validate_config'),patch.object(reset.inv,'inventory_data',return_value={}),patch.object(reset.inv,'validate_inventory'),patch.object(reset.inv,'ensure_agent'),patch.object(reset.tools,'Runner',side_effect=factory),patch.object(reset.getpass,'getpass',side_effect=['SUDO-SECRET1','SUDO-SECRET2','SUDO-SECRET3']):
                if failure:
                    with self.assertRaises(RuntimeError):reset.execute(logs.append,work)
                else:
                    reset.execute(logs.append,work)
                    with self.assertRaisesRegex(RuntimeError,'receipt already exists'):reset.execute(logs.append,work)
            self.assertNotIn('SECRET','\n'.join(logs)+str(commands))
            if failure:return calls,logs
            self.assertEqual((config/'passwords.yml').read_text(),'PASSWORD-SECRET')
            self.assertEqual((config/'certificates/ca').read_text(),'CA-BYTES')
            self.assertFalse((config/'clouds.yaml').exists());self.assertFalse((phase4/'networks.json').exists())
            self.assertEqual((phase4/'cache.img').read_text(),'IMAGE-CACHE')
            import yaml
            self.assertTrue(yaml.safe_load((config/'globals.yml').read_text())['enable_neutron_agent_ha'])
            receipt=json.loads((root/'private/ha-reset.json').read_text());self.assertEqual(receipt['status'],'complete')
            return calls,logs
    def test_inspect_all_before_destruction_archive_state_preserve_credentials(self):
        calls,logs=self.simulate()
        self.assertEqual(calls,['before:node1','before:node2','before:node3','destroy:node1','after:node1','destroy:node2','after:node2','destroy:node3','after:node3'])
        self.assertIn('RESULT: FRESH_HA_INSTALL_READY',logs)
    def test_failed_initial_inspection_never_destroys_any_node(self):
        calls,_=self.simulate('before:node3');self.assertFalse(any(c.startswith('destroy') for c in calls))
    def test_destroy_failure_stops_before_other_nodes_no_retry(self):
        calls,logs=self.simulate('destroy:node2')
        self.assertEqual(calls.count('destroy:node2'),1);self.assertNotIn('destroy:node3',calls)
        self.assertNotIn('RESULT: FRESH_HA_INSTALL_READY',logs)
    def test_command_limits_exact_node_and_preserves_options(self):
        args=list(map(str,reset.destroy_command(Path('/inventory'),Path('/config'),Path('/options'),'node2')))
        self.assertIn('--yes-i-really-really-mean-it',args);self.assertEqual(args[args.index('--limit')+1],'node2')
        self.assertNotIn('deploy',args);self.assertNotIn('wipefs',args)
        with self.assertRaises(RuntimeError):reset.destroy_command(None,None,None,'r2d2')
    def test_private_state_is_atomic_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state';reset.save_state(path,{'status':'pending'})
            self.assertEqual(json.loads(path.read_text())['status'],'pending');self.assertEqual(path.stat().st_mode&0o777,0o600)
            other=Path(folder)/'link';other.symlink_to(path)
            with self.assertRaises(RuntimeError):reset.save_state(other,{})


class HAContractTests(unittest.TestCase):
    def test_settings_are_explicit_three_node_ha(self):
        x=reset.config_step.settings(Path('/config'))
        for k in ('enable_haproxy','enable_keepalived','enable_mariadb','enable_neutron_agent_ha'):self.assertIs(x[k],True)
        self.assertEqual(x['max_l3_agents_per_router'],3);self.assertEqual(x['dhcp_agents_per_network'],2);self.assertFalse(x['enable_neutron_dvr'])
    def test_router_request_explicitly_ha_and_non_distributed(self):
        spec=net.specifications({'external_subnet':'subnet','external_network':'net'},'project')['router']
        self.assertIs(spec['ha'],True);self.assertIs(spec['distributed'],False)
    def test_placement_requires_one_active_two_standby_on_distinct_known_nodes(self):
        agents=[{'host':n,'ha_state':'active' if n=='node2' else 'standby','alive':True,'admin_state_up':True} for n in ('node1','node2','node3')]
        self.assertEqual(len(net.ha_placement(agents)),3)
        for field,value in [('ha_state','active'),('host','node2'),('alive',False),('ha_state',None)]:
            bad=copy.deepcopy(agents);bad[0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(RuntimeError):net.ha_placement(bad)
    def test_pinned_neutron_template_renders_ha_values(self):
        import jinja2
        source=(ROOT/'build/real-tools-check/share/kolla-ansible/ansible/roles/neutron/templates/neutron.conf.j2').read_text()
        block=source[source.index('{% if enable_neutron_agent_ha'):];block=block[:block.index('{% endif %}')+len('{% endif %}')]
        env=jinja2.Environment(undefined=jinja2.StrictUndefined);env.filters['bool']=bool
        values=reset.config_step.settings(Path('/config'))
        rendered=env.from_string(block).render(**values)
        for line in ('l3_ha = true','max_l3_agents_per_router = 3','dhcp_agents_per_network = 2'):self.assertIn(line,rendered)
    def recap(self,ignored=0):
        return ''.join('TASK ['+m+' fixture]\n' for m in ('mariadb :','keystone :','nova-cell :','neutron :','cinder :','horizon :'))+'PLAY RECAP\n'+''.join(n+f' : ok=1 changed=1 unreachable=0 failed=0 rescued=0 ignored={ignored}\n' for n in deploy.inv.NODES)
    def test_only_exact_reviewed_discovery_timeouts_can_be_ignored(self):
        evidence='TASK [mariadb : Check MariaDB service port liveness]\n'
        for n in deploy.inv.NODES:evidence+=f'fatal: [{n}]: FAILED! => {{"msg":"Timeout when waiting for search string MariaDB in address:3306"}}\n[{n}]: ...ignoring\n'
        self.assertEqual(len(deploy.deployment_recap(evidence+self.recap(1))),3)
        for text in [self.recap(1),evidence.replace('port liveness','wrong task')+self.recap(1),evidence.replace('Timeout when waiting for search string MariaDB','Other failure')+self.recap(1)]:
            with self.assertRaises(RuntimeError):deploy.deployment_recap(text)
    def test_galera_parser_requires_all_expected_status_keys(self):
        values={'wsrep_cluster_status':'Primary','wsrep_cluster_size':'3','wsrep_connected':'ON','wsrep_ready':'ON','wsrep_local_state_comment':'Synced','wsrep_cluster_state_uuid':'12345678-1234-1234-1234-123456789abc'}
        text='\n'.join(k+'\t'+v for k,v in values.items())
        with patch.object(observe,'run',return_value=text):self.assertTrue(observe.galera_status('192.168.0.200')[1])
        with patch.object(observe,'run',return_value=text.replace('Primary','Non-Primary')):self.assertFalse(observe.galera_status('192.168.0.200')[1])
        with patch.object(observe,'run',return_value='wsrep_ready\tON'),self.assertRaises(RuntimeError):observe.galera_status('192.168.0.200')

if __name__=='__main__':unittest.main()
