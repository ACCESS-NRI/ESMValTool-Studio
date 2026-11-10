import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from esmval_gui import configuration, remote


class ExecutionTests(unittest.TestCase):
    def test_local_job_cannot_execute_on_a_workstation(self):
        with patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "ssh"}), patch.object(remote, "ssh") as ssh:
            with self.assertRaisesRegex(RuntimeError, "Open Studio on ARE"):
                remote.execute("gadi", "qdel 123.gadi-pbs", transport="local")
            ssh.assert_not_called()

    def test_ssh_record_keeps_its_transport_on_are(self):
        with patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local"}), patch.object(remote, "ssh", return_value="done") as ssh:
            self.assertEqual(remote.execute("other-account@gadi", "qstat", transport="ssh"), "done")
            ssh.assert_called_once()

    @unittest.skipIf(sys.platform == "win32", "Direct PBS uses the ARE POSIX shell")
    @patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local"})
    def test_local_configuration_and_command_never_use_ssh(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(remote, "ssh", side_effect=AssertionError("SSH used")):
            path = Path(temporary) / "config.yml"
            path.write_text("output_dir: /tmp\n")
            self.assertEqual(configuration.load_remote_files("gadi", str(path))[0]["content"], path.read_text())
            self.assertEqual(remote.execute("ignored", "cat", "Température"), "Température")
            with self.assertRaisesRegex(RuntimeError, "deliberate failure"):
                remote.execute("ignored", "echo 'deliberate failure' >&2; exit 3")

    @unittest.skipIf(sys.platform == "win32", "Direct PBS uses the ARE POSIX shell")
    @patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local"})
    def test_submission_script_logs_and_exit_status_with_fake_pbs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(remote, "ssh", side_effect=AssertionError("SSH used")):
            root = Path(temporary)
            binary = root / "bin"
            binary.mkdir()
            scripts = {
                "qsub": '#!/bin/bash\nbash "$1"\nprintf "12345.gadi-pbs\\n"\n',
                "qstat": '#!/bin/bash\necho "Job has expired" >&2\nexit 1\n',
                "esmvaltool": '#!/bin/bash\nprintf "diagnostic output\\n"\nprintf "diagnostic warning\\n" >&2\nexit 7\n',
            }
            for name, source in scripts.items():
                path = binary / name
                path.write_text(source)
                path.chmod(0o700)
            settings = remote.RemoteSettings(project="ab12", work_dir=str(root / "runs"),
                                             esmvaltool_command=str(binary / "esmvaltool"))
            with patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}):
                record = remote.submit(settings, "documentation: {}\ndiagnostics: {}\n", root / "state")
                self.assertEqual(record["transport"], "local")
                self.assertEqual(record["output_dir"], record["remote_dir"] + "/output")
                self.assertEqual(remote.status(record)["state"], "failed")
                self.assertEqual(remote.status(record)["exit_status"], 7)
                logs = remote.logs(record)
                self.assertIn("diagnostic output", logs["stdout"])
                self.assertIn("diagnostic warning", logs["stderr"])
                self.assertEqual((Path(record["remote_dir"]) / ".studio-exit-code").read_text().strip(), "7")
                self.assertEqual(remote.read_job(root / "state", record["id"]), record)

    def test_script_keeps_outputs_in_run_and_adds_required_storage(self):
        settings = remote.RemoteSettings(project="ab12", work_dir="/scratch/ab12/user/jobs",
                                         storage="gdata/data1", esmvaltool_command="/g/data/runtime/bin/esmvaltool")
        script = remote.script_for(settings, settings.work_dir + "/run/recipe.yml", settings.work_dir + "/run")
        self.assertIn("storage=gdata/data1+scratch/ab12+gdata/runtime", script)
        self.assertIn("--output_dir /scratch/ab12/user/jobs/run/output", script)
        self.assertIn("exec > stdout.txt 2> stderr.txt", script)

    @patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local"})
    def test_missing_are_mount_is_actionable(self):
        settings = remote.RemoteSettings(project="ab12", work_dir="/scratch/nonexistent_studio_test/user/jobs")
        checks = remote.check_local_paths(settings)
        self.assertTrue(any(item["level"] == "error" and "ARE Storage" in item["detail"] for item in checks))


if __name__ == "__main__":
    unittest.main()
