import unittest
from unittest.mock import patch

from esmval_gui.app import ConfigFile, ConfigInspect, inspect_config
from esmval_gui.configuration import inspect_files, load_remote_files


class ConfigurationTests(unittest.TestCase):
    def test_nested_files_merge_and_report_origins(self):
        result = inspect_files([
            {"name": "01-default.yml", "content": "output_dir: /scratch/base\nprojects:\n  CMIP6:\n    data:\n      native6:\n        rootpath: /g/data/base\n        type: DRS\nsearch_data: [data, extra]\n"},
            {"name": "02-site.yaml", "content": "projects:\n  CMIP6:\n    data:\n      native6:\n        rootpath: /g/data/site\n      replica:\n        rootpath: /g/data/replica\nmax_parallel_tasks: 4\n"},
            {"name": "03-user.yml", "content": "search_data: [data]\noutput_dir: /scratch/user\n"},
        ])
        effective = result["effective"]
        self.assertEqual(effective["projects"]["CMIP6"]["data"]["native6"],
                         {"rootpath": "/g/data/site", "type": "DRS"})
        self.assertEqual(effective["search_data"], ["data"])
        self.assertEqual(effective["output_dir"], "/scratch/user")
        self.assertEqual(result["counts"]["data_sources"], 2)
        origins = {tuple(item["path"]): item["file"] for item in result["origins"]}
        self.assertEqual(origins[("projects", "CMIP6", "data", "native6", "type")], "01-default.yml")
        self.assertEqual(origins[("projects", "CMIP6", "data", "native6", "rootpath")], "02-site.yaml")
        self.assertEqual(origins[("search_data",)], "03-user.yml")
        self.assertEqual({tuple(item["path"]) for item in result["overrides"]},
                         {("projects", "CMIP6", "data", "native6", "rootpath"), ("search_data",), ("output_dir",)})

    def test_invalid_and_cyclic_yaml(self):
        for source in ("[not, a, mapping]", "key: [unfinished", "cycle: &node {self: *node}", "key: .nan"):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    inspect_files([{"name": "bad.yml", "content": source}])

    def test_remote_reader_validates_host_and_path(self):
        with self.assertRaises(ValueError):
            load_remote_files("-bad", "")
        with self.assertRaises(ValueError):
            load_remote_files("gadi", "relative/config")
        with patch("esmval_gui.configuration.remote.ssh", return_value='[{"name":"/home/u/.config/esmvaltool/01.yml","content":"output_dir: /tmp"}]') as ssh:
            files = load_remote_files("gadi", "/home/u/.config/esmvaltool")
        self.assertEqual(files[0]["name"], "/home/u/.config/esmvaltool/01.yml")
        self.assertEqual(ssh.call_args.kwargs["timeout"], 45)

    def test_api_inspects_files(self):
        result = inspect_config(ConfigInspect(files=[
            ConfigFile(name="a.yml", content="output_dir: /tmp"),
            ConfigFile(name="b.yml", content="output_dir: /scratch"),
        ]))
        self.assertEqual(result["effective"]["output_dir"], "/scratch")


if __name__ == "__main__":
    unittest.main()
