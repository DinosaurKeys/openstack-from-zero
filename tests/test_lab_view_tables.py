import contextlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('view_table_tests',ROOT/'scripts/lab-view.py')
view=importlib.util.module_from_spec(spec);spec.loader.exec_module(view)


class ViewingTableTests(unittest.TestCase):
    def test_server_inventory_shows_api_state_and_compute_host_without_mutation(self):
        class API:
            def list(self,service,path,key):
                if (service,path,key)!=('compute','servers/detail?limit=100&all_tenants=1','servers'):raise AssertionError('Unexpected route')
                return [{'id':'vm-id','name':'lab-vm-01','status':'SHUTOFF','OS-EXT-SRV-ATTR:host':'node2','tenant_id':'project-id','private':'withheld'}]
            def write(self,*args):raise AssertionError('Inventory must not write')
        result=view.collect(API(),'servers',True);text=view.table('servers',result)
        for value in ('Name','Status (Nova)','Compute host','VM ID','lab-vm-01','SHUTOFF','node2','vm-id'):
            self.assertIn(value,text)
        self.assertEqual(result['servers'][0]['tenant_id'],'project-id')
        self.assertNotIn('withheld',str(result))

    def test_volume_inventory_keeps_storage_backend_separate_from_attachment_compute(self):
        class API:
            def list(self,service,path,key):
                if (service,path,key)!=('block-storage','volumes/detail?limit=100&all_tenants=1','volumes'):raise AssertionError('Unexpected route')
                return [{'id':'volume-id','name':'data','size':1,'status':'in-use','os-vol-host-attr:host':'node3@lvm-1#lvm-1',
                  'attachments':[{'server_id':'vm-id','host_name':'node1','device':'/dev/vdb'}]}]
        text=view.table('volumes',view.collect(API(),'volumes',True))
        self.assertIn('Storage backend',text);self.assertIn('node3@lvm-1#lvm-1',text);self.assertIn('vm-id',text)
        self.assertNotIn('node1',text)

    def test_default_inventory_keeps_existing_project_scope_and_unknown_host_is_not_invented(self):
        class API:
            def list(self,service,path,key):
                if path!='servers/detail?limit=100':raise AssertionError('Scope changed')
                return [{'id':'vm-id','name':'vm','status':'ACTIVE'}]
        text=view.table('servers',view.collect(API(),'servers'))
        self.assertIn('unknown',text);self.assertNotIn('node1',text)
        text=view.table('volumes',{'volumes':[{'id':'v1','attachments':[]},{'id':'v2','attachments':None}]})
        self.assertRegex(text.splitlines()[1],r'\| -\s*\| v1$')
        self.assertIn('unknown',text.splitlines()[2])

    def test_disabled_reason_is_retained_in_json_and_table_without_mutation(self):
        class API:
            def list(self,service,path,key):
                if (service,path,key)!=('compute','os-services','services'):raise AssertionError('Unexpected route')
                return [{'host':'node2','binary':'nova-compute','status':'disabled','state':'down',
                  'disabled_reason':'AUTO: Connection to libvirt lost','unselected_private_field':'private'}]
        result=view.collect(API(),'compute-services')
        self.assertEqual(result['services'][0]['disabled_reason'],'AUTO: Connection to libvirt lost')
        text=view.table('compute-services',result)
        self.assertIn('Disabled reason',text);self.assertIn('AUTO: Connection to libvirt lost',text)
        self.assertNotIn('unselected_private_field',str(result))

    def test_agents_liveness_and_admin_state_are_separate(self):
        data={'agents':[{'host':'node1','agent_type':'L3 agent','alive':True,'admin_state_up':False},
          {'host':'node2','agent_type':'DHCP agent','alive':False,'admin_state_up':True}]}
        text=view.table('agents',data)
        self.assertIn('Alive',text);self.assertIn('Enabled',text)
        self.assertRegex(text.splitlines()[1],r'yes\s*\| no$')
        self.assertRegex(text.splitlines()[2],r'no\s*\| yes$')

    def test_service_tables_keep_enabled_and_heartbeat_distinct(self):
        for name in ('compute-services','cinder-services'):
            text=view.table(name,{'services':[{'host':'node1','binary':'worker','status':'enabled','state':'down'}]})
            self.assertIn('Heartbeat',text);self.assertIn('enabled',text);self.assertIn('down',text)

    def test_enabled_service_without_disable_reason_shows_dash(self):
        text=view.table('compute-services',{'services':[{'host':'node1','binary':'nova-compute','status':'enabled','state':'up','disabled_reason':None}]})
        self.assertTrue(text.splitlines()[1].endswith('| -'))
        self.assertNotIn('unknown',text)
        text=view.table('compute-services',{'services':[{'host':'node2','binary':'nova-compute','status':'disabled','state':'down'}]})
        self.assertIn('unknown',text)

    def test_new_views_are_read_queries_and_fields_selected(self):
        for name in ('hypervisors','cinder-services'):
            service,path,key,fields=view.QUERIES[name]
            class API:
                def list(self,s,p,k):
                    if (s,p,k)!=(service,path,key):raise AssertionError('wrong API route')
                    return [{'host':'node1','hypervisor_hostname':'node1','state':'up','status':'enabled','password':'must-not-leave'}]
            result=view.collect(API(),name)
            self.assertNotIn('password',str(result));self.assertIn('node1',view.table(name,result))

    def test_empty_tables_and_terminal_control_characters(self):
        self.assertIn('Host',view.table('hypervisors',{'hypervisors':[]}))
        text=view.table('hypervisors',{'hypervisors':[{'hypervisor_hostname':'node1\x1b[2J\nrow'}]})
        self.assertNotIn('\x1b',text);self.assertEqual(len(text.splitlines()),2)

    def test_reexec_preserves_table_option(self):
        class Reexec(Exception):pass
        with patch.object(view.step.deploy.validation.config_step,'load_step',return_value=SimpleNamespace(require_r2d2=lambda:None)),patch.object(view.step.deploy.tools,'check_paths'),patch.object(view.step.deploy.tools,'tool_environment',return_value={}),patch.object(view.os,'execve',side_effect=Reexec) as execute:
            with self.assertRaises(Reexec):view.main(['agents','--table'])
        self.assertEqual(execute.call_args.args[1][-2:],['agents','--table'])

    def test_table_rejects_unsupported_objects_before_connection(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):view.main(['router','--table'])

    def test_all_projects_flag_survives_reexec_and_unsupported_views_cannot_use_it(self):
        class Reexec(Exception):pass
        with patch.object(view.step.deploy.validation.config_step,'load_step',return_value=SimpleNamespace(require_r2d2=lambda:None)),patch.object(view.step.deploy.tools,'check_paths'),patch.object(view.step.deploy.tools,'tool_environment',return_value={}),patch.object(view.os,'execve',side_effect=Reexec) as execute:
            with self.assertRaises(Reexec):view.main(['servers','--table','--all-projects'])
        self.assertEqual(execute.call_args.args[1][-3:],['servers','--table','--all-projects'])
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):view.main(['agents','--all-projects'])
        with self.assertRaisesRegex(RuntimeError,'only for servers and volumes'):view.collect(None,'router',True)


if __name__=='__main__':unittest.main()
