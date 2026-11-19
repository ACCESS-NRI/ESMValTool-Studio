import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from esmval_gui import app, recipe_monitor, remote


class RecipeMonitorTests(unittest.TestCase):
    def test_default_allowlist_and_explicit_empty_disable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(recipe_monitor.allowed_users(), {"rb5533"})
        with patch.dict(os.environ, {"ESMVAL_GUI_RECIPE_MONITOR_USERS": "rb5533, ab1234"}):
            self.assertEqual(recipe_monitor.allowed_users(), {"rb5533", "ab1234"})
        with patch.dict(os.environ, {"ESMVAL_GUI_RECIPE_MONITOR_USERS": ""}):
            self.assertEqual(recipe_monitor.allowed_users(), set())

    @patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "ssh"})
    def test_ssh_identity_uses_fixed_gadi_host_and_actual_account(self):
        with patch.object(remote, "execute", return_value="ab1234\n") as execute:
            self.assertEqual(recipe_monitor.identity("rb5533"), "ab1234")
            execute.assert_called_once_with("rb5533@gadi.nci.org.au", "/usr/bin/id -un",
                                            timeout=15, transport="ssh")

    @unittest.skipUnless(hasattr(os, "getuid"), "ARE uses POSIX identity")
    @patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local", "USER": "rb5533"})
    def test_are_identity_ignores_browser_and_environment_username(self):
        import pwd
        with patch.object(pwd, "getpwuid") as user, patch.object(remote, "execute") as execute:
            user.return_value.pw_name = "ab1234"
            self.assertEqual(recipe_monitor.identity("rb5533"), "ab1234")
            user.assert_called_once_with(os.getuid())
            execute.assert_not_called()

    def test_denied_identity_cannot_read_monitor(self):
        with patch.object(recipe_monitor, "identity", return_value="ab1234"), \
                patch.object(recipe_monitor, "allowed_users", return_value={"rb5533"}), \
                patch.object(recipe_monitor, "overview") as overview:
            for endpoint in (app.monitor_access, app.monitor_overview):
                with self.assertRaises(HTTPException) as error:
                    endpoint(app.MonitorInput(username="rb5533"))
                self.assertEqual(error.exception.status_code, 403)
                self.assertIn("ab1234", error.exception.detail)
            overview.assert_not_called()

    def test_identity_failure_denies_access(self):
        with patch.object(recipe_monitor, "identity", side_effect=RuntimeError("SSH failed")):
            with self.assertRaises(HTTPException) as error:
                app.monitor_access(app.MonitorInput())
            self.assertEqual(error.exception.status_code, 403)
            self.assertIn("SSH failed", error.exception.detail)

    def test_authorized_identity_can_read_monitor(self):
        with patch.object(recipe_monitor, "identity", return_value="rb5533"), \
                patch.object(recipe_monitor, "allowed_users", return_value={"rb5533"}), \
                patch.object(recipe_monitor, "overview", return_value={"recipes": []}) as overview:
            self.assertTrue(app.monitor_access(app.MonitorInput())["allowed"])
            self.assertEqual(app.monitor_overview(app.MonitorInput()), {"recipes": []})
            overview.assert_called_once_with(app.STATE, "rb5533")

    @patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local"})
    def test_latest_by_path_across_full_history_not_title_or_fifty_run_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(60):
                record = {"id": f"{index:012x}", "recipe_path": "a/recipe_same.yml",
                          "title": "Same title", "submitted_at": f"2026-10-01T00:{index:02d}:00",
                          "transport": "local", "last_status": {"state": "finished"}}
                (root / f"{record['id']}.json").write_text(json.dumps(record))
            record.update(id="00000000003c", recipe_path="b/recipe_same.yml", cancelled=True)
            (root / f"{record['id']}.json").write_text(json.dumps(record))
            (root / "ffffffffffff.json").write_text(json.dumps({"transport": "local"}))
            (root / "eeeeeeeeeeee.json").write_text("bad json")
            library = [{"path": name} for name in ("a/recipe_same.yml", "b/recipe_same.yml", "recipe_new.yml")]
            with patch.object(recipe_monitor.recipes, "available_recipe_root", return_value=root), \
                    patch.object(recipe_monitor.recipes, "list_recipes", return_value=library):
                data = recipe_monitor.overview(root, "rb5533")
            self.assertEqual(data["recipes"][0]["last_run"]["id"], "00000000003b")
            self.assertEqual(data["recipes"][1]["last_run"]["state"], "cancelled")
            self.assertIsNone(data["recipes"][2]["last_run"])
            self.assertEqual(data["unlinked_runs"], 1)

    def test_invalid_library_link_rejected_before_submission(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(app.recipes, "available_recipe_root", return_value=Path(tmp)), \
                patch.object(remote, "submit") as submit:
            with self.assertRaises(HTTPException) as error:
                app.submit(app.RunInput(yaml="", settings=remote.RemoteSettings(), recipe_path="../outside.yml"))
            self.assertEqual(error.exception.status_code, 400)
            submit.assert_not_called()


class ResourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.record = {"id": "123456abcdef", "host": "gadi.nci.org.au", "job_id": "123.gadi-pbs",
                       "remote_dir": "/scratch/ab12/rb5533/run", "transport": "ssh"}
        (self.root / "123456abcdef.json").write_text(json.dumps(self.record))

    def test_pbs_resources_are_distinct_from_requests_and_survive_expiry(self):
        output = """job_state = F
    Exit_status = 0
    Resource_List.ncpus = 8
    Resource_List.mem = 32gb
    Resource_List.walltime = 02:00:00
    resources_used.cput = 00:31:02
    resources_used.mem = 1048576kb
    resources_used.walltime = 00:05:10
    resources_used.jobfs = 123kb
"""
        with patch.object(remote, "execute", return_value=output):
            snapshot = remote.refresh_status(self.root, self.record["id"])
        self.assertEqual(snapshot["resources_used"]["mem"], "1048576kb")
        self.assertEqual(snapshot["requested_resources"]["mem"], "32gb")
        self.assertEqual(snapshot["resources_used"]["jobfs"], "123kb")
        self.assertEqual(snapshot["state"], "finished")
        with patch.object(remote, "execute", side_effect=[RuntimeError("expired"), "0"]):
            expired = remote.refresh_status(self.root, self.record["id"])
        self.assertEqual(expired["resources_used"], snapshot["resources_used"])
        self.assertEqual(expired["usage_checked_at"], snapshot["usage_checked_at"])
        self.assertEqual(expired["state"], "finished")

    def test_failed_refresh_retains_terminal_state_but_not_running_state(self):
        for previous, expected in (("finished", "finished"), ("running", "unknown")):
            remote.update_record(self.root, self.record["id"], {"last_status": {"state": previous}})
            with patch.object(remote, "execute", side_effect=RuntimeError("offline")):
                snapshot = remote.refresh_status(self.root, self.record["id"])
            self.assertEqual(snapshot["state"], expected)
            self.assertTrue(snapshot["stale"])

    def test_queued_job_has_no_fabricated_usage(self):
        with patch.object(remote, "execute", return_value="job_state = Q"):
            snapshot = remote.refresh_status(self.root, self.record["id"])
        self.assertEqual(snapshot["resources_used"], {})
        self.assertIsNone(snapshot["usage_checked_at"])

    def test_submission_records_library_path_and_requested_resources(self):
        settings = remote.RemoteSettings(project="ab12", work_dir="/scratch/ab12/rb5533/runs")
        with patch.object(remote, "execute", side_effect=["", "", "", "123.gadi-pbs"]):
            record = remote.submit(settings, "recipe", self.root, recipe_path="ocean/recipe_test.yml")
        self.assertEqual(record["recipe_path"], "ocean/recipe_test.yml")
        self.assertEqual(record["requested_resources"]["mem"], "4GB")
        self.assertNotIn("resources_used", record)

    def test_poll_cannot_undo_concurrent_cancellation(self):
        def during_poll(_):
            remote.update_record(self.root, self.record["id"], {"cancelled": True})
            return {**self.record, "state": "running"}
        with patch.object(remote, "status", side_effect=during_poll):
            self.assertEqual(remote.refresh_status(self.root, self.record["id"])["state"], "cancelled")
        self.assertTrue(remote.read_job(self.root, self.record["id"])["cancelled"])


class Analysis3Tests(unittest.TestCase):
    def test_selected_version_replaces_stale_runtime_and_adds_storage(self):
        settings = remote.RemoteSettings(project="ab12", work_dir="/scratch/ab12/rb5533/runs",
                                         analysis3_module="conda/analysis3-26.10",
                                         setup_command="module load conda/analysis3-25.06",
                                         esmvaltool_command="/old/environment/bin/esmvaltool")
        self.assertEqual(settings.esmvaltool_command, "esmvaltool")
        self.assertIn("module load conda/analysis3-26.10", settings.setup_command)
        self.assertNotIn("25.06", settings.setup_command)
        script = remote.script_for(settings, settings.work_dir + "/recipe.yml", settings.work_dir)
        self.assertIn("gdata/xp65", script)
        self.assertIn("module purge", script)
        self.assertNotIn("/old/environment", script)

    def test_module_names_cannot_inject_shell_commands(self):
        for name in ("conda/analysis3; touch /tmp/bad", "conda/analysis3\nfalse", "other/module", "conda/analysis3-$(id)"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                remote.RemoteSettings(analysis3_module=name)

    def test_custom_runtime_is_preserved(self):
        settings = remote.RemoteSettings(setup_command="source /custom/activate", esmvaltool_command="/custom/bin/esmvaltool")
        self.assertEqual(settings.setup_command, "source /custom/activate")
        self.assertEqual(settings.esmvaltool_command, "/custom/bin/esmvaltool")

    def test_available_modules_are_parsed_and_deduplicated(self):
        output = """/g/data/xp65/public/modules:
conda/analysis3-26.10(default)
conda/analysis3-25.06
conda/analysis3-26.10
conda/analysis3
other/module
"""
        with patch.object(remote, "execute", return_value=output) as execute:
            self.assertEqual(set(remote.analysis3_versions("rb5533")),
                             {"conda/analysis3", "conda/analysis3-25.06", "conda/analysis3-26.10"})
            self.assertEqual(execute.call_args.args[0], "rb5533@gadi.nci.org.au")
            self.assertIn("module -t avail conda/analysis3", execute.call_args.args[1])

    def test_module_discovery_is_account_gated(self):
        with patch.object(recipe_monitor, "identity", return_value="ab1234"), \
                patch.object(recipe_monitor, "allowed_users", return_value={"rb5533"}), \
                patch.object(remote, "analysis3_versions") as versions:
            with self.assertRaises(HTTPException) as error:
                app.monitor_analysis3(app.MonitorInput(username="rb5533"))
            self.assertEqual(error.exception.status_code, 403)
            versions.assert_not_called()

    def test_probe_uses_selected_module_without_personal_environment_fallback(self):
        with patch.object(remote, "probe", return_value={}) as probe:
            app.probe(app.ProbeInput(analysis3_module="conda/analysis3-26.10",
                                     esmvaltool_command="/old/bin/esmvaltool"))
            self.assertEqual(probe.call_args.args[1], "esmvaltool")
            self.assertIn("module load conda/analysis3-26.10", probe.call_args.args[2])
            self.assertFalse(probe.call_args.kwargs["allow_fallback"])

    def test_no_personal_environment_discovery_for_pinned_runtime(self):
        with patch.object(remote, "execute", return_value="PBS=yes\n") as execute:
            remote.probe("gadi.nci.org.au", setup="module load conda/analysis3-26.10", allow_fallback=False)
            self.assertNotIn(".conda/environments.txt", execute.call_args.args[1])

    def test_run_record_keeps_selected_version(self):
        settings = remote.RemoteSettings(project="ab12", work_dir="/scratch/ab12/rb5533/runs",
                                         analysis3_module="conda/analysis3-26.10")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(remote, "execute", side_effect=["", "", "", "123.gadi-pbs"]):
            record = remote.submit(settings, "recipe", Path(tmp))
            self.assertEqual(remote.read_job(Path(tmp), record["id"])["analysis3_module"], "conda/analysis3-26.10")


if __name__ == "__main__":
    unittest.main()
