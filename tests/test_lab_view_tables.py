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


if __name__=='__main__':unittest.main()
