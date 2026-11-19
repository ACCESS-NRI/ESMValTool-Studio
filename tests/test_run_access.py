import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from esmval_gui import app, recipe_monitor, remote


class RunAccessTests(unittest.TestCase):
    def protected_endpoints(self):
        return [
            (app.jobs, ()),
            (app.job, ('aabbccddeeff',)),
            (app.job_logs, ('aabbccddeeff',)),
            (app.cancel_job, ('aabbccddeeff',)),
            (app.job_outputs, ('aabbccddeeff',)),
            (app.job_output, ('aabbccddeeff', 'index.html')),
            (app.job_report, ('aabbccddeeff', 'index.html')),
            (app.report_file, ('aabbccddeeff', app.outputs.report_ticket('aabbccddeeff'), 'index.html')),
            (app.monitor_access, (app.MonitorInput(username='rb5533'),)),
            (app.monitor_overview, (app.MonitorInput(username='rb5533'),)),
            (app.monitor_analysis3, (app.MonitorInput(username='rb5533'),)),
        ]

    def assert_all_denied(self):
        with patch.object(remote, 'execute') as execute, \
                patch.object(remote, 'read_job') as read_job, \
                patch.object(remote, 'refresh_status') as refresh, \
                patch.object(remote, 'cancel') as cancel, \
                patch.object(recipe_monitor, 'overview') as overview:
            for endpoint, args in self.protected_endpoints():
                with self.subTest(endpoint=endpoint.__name__), self.assertRaises(HTTPException) as error:
                    endpoint(*args)
                self.assertEqual(error.exception.status_code, 403)
            for operation in (execute, read_job, refresh, cancel, overview):
                operation.assert_not_called()

    def test_every_run_endpoint_rejects_standalone_even_with_allowlisted_claims(self):
        for execution in ('ssh', 'local'):
            with self.subTest(execution=execution), patch.dict(os.environ, {
                'ESMVAL_GUI_EXECUTION': execution, 'USER': 'rb5533', 'LOGNAME': 'rb5533',
                'ESMVAL_GUI_RECIPE_MONITOR_USERS': 'rb5533',
            }, clear=True):
                self.assert_all_denied()

    def test_are_marker_requires_direct_execution_and_proxy_xsrf_protection(self):
        for execution, xsrf in [('ssh', '1'), ('local', ''), ('local', '0')]:
            with self.subTest(execution=execution, xsrf=xsrf), patch.dict(os.environ, {
                'ESMVAL_GUI_DEPLOYMENT': 'are', 'ESMVAL_GUI_EXECUTION': execution,
                'ESMVAL_GUI_REQUIRE_XSRF': xsrf,
            }, clear=True):
                self.assertFalse(recipe_monitor.is_are())
                self.assert_all_denied()

    @unittest.skipUnless(hasattr(os, 'geteuid'), 'ARE uses POSIX account identity')
    def test_actual_are_account_controls_all_endpoints_not_browser_or_environment(self):
        import pwd
        with patch.dict(os.environ, {'ESMVAL_GUI_DEPLOYMENT': 'are',
                'ESMVAL_GUI_EXECUTION': 'local', 'ESMVAL_GUI_REQUIRE_XSRF': '1',
                'USER': 'rb5533', 'LOGNAME': 'rb5533', 'ESMVAL_GUI_RECIPE_MONITOR_USERS': 'rb5533'}, clear=True), \
                patch.object(pwd, 'getpwuid') as user:
            user.return_value.pw_name = 'ab1234'
            self.assert_all_denied()
            user.return_value.pw_name = 'rb5533'
            self.assertEqual(app.monitor_access(app.MonitorInput(username='ab1234')),
                             {'user': 'rb5533', 'allowed': True})
            self.assertTrue(app.health()['run_access']['allowed'])
            with patch.dict(os.environ, {'ESMVAL_GUI_RECIPE_MONITOR_USERS': ''}):
                self.assert_all_denied()

    def test_health_denies_locally_without_contacting_gadi(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(remote, 'execute') as execute:
            health = app.health()
            self.assertFalse(health['are_session'])
            self.assertFalse(health['run_access']['allowed'])
            execute.assert_not_called()

    def test_account_lookup_failure_is_a_denial(self):
        for error in (KeyError('missing UID'), OSError('account lookup unavailable')):
            with self.subTest(error=error), patch.object(recipe_monitor, 'identity', side_effect=error):
                self.assert_all_denied()
                self.assertFalse(app.health()['run_access']['allowed'])

    def test_standalone_launcher_clears_inherited_are_marker(self):
        from esmval_gui import __main__ as cli
        with patch.dict(os.environ, {'ESMVAL_GUI_DEPLOYMENT': 'are'}), \
                patch('sys.argv', ['esmval_gui', '--no-browser']), \
                patch.object(cli.uvicorn, 'run') as run, patch('builtins.print'):
            cli.main()
            self.assertNotIn('ESMVAL_GUI_DEPLOYMENT', os.environ)
            run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
