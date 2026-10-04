import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from esmval_gui.catalogue import get_catalogue
from esmval_gui.esgf import normalize_results, search_datasets
from esmval_gui.recipes import (add_script, available_recipe_root, build_recipe, create_node, create_profile, edit_profile, edit_profile_fields,
                                edit_node, edit_node_fields, list_recipes, move_profile_step, parse_recipe, read_library_recipe,
                                read_node, library_recipe_info,
                                set_profile_order, set_variable_profile, summarize)
from esmval_gui.remote import RemoteSettings, cancel, script_for, submit


class RecipeTests(unittest.TestCase):
    def test_esgf_results_fill_recipe_facets(self):
        payload = {"response": {"numFound": 2, "docs": [
            {"source_id": "ACCESS-ESM1-5", "experiment_id": "historical", "member_id": "r1i1p1f1",
             "grid_label": "gn", "table_id": "Amon", "variable_id": "tas", "version": "20200101", "id": "opaque"},
            {"source_id": "ACCESS-ESM1-5", "experiment_id": "historical", "member_id": "r1i1p1f1",
             "grid_label": "gn", "table_id": "Amon", "variable_id": "tas", "version": "20200101"}]}}
        result = normalize_results(payload, "CMIP6")
        self.assertEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertEqual((item["dataset"], item["exp"], item["ensemble"], item["grid"]),
                         ("ACCESS-ESM1-5", "historical", "r1i1p1f1", "gn"))
        source, _ = create_node("documentation: {title: Example}\n", "dataset", item["dataset"],
                                project=item["project"], exp=item["exp"], ensemble=item["ensemble"], grid=item["grid"])
        self.assertEqual(parse_recipe(source)["datasets"][0]["ensemble"], "r1i1p1f1")
        self.assertEqual(parse_recipe(source)["datasets"][0]["grid"], "gn")
        cmip5 = normalize_results({"response": {"docs": [{"model": "ACCESS1.0", "experiment": "historical",
                                                         "ensemble": "r1i1p1", "cmor_table": "Amon"}]}}, "CMIP5")
        self.assertEqual(cmip5["results"][0]["dataset"], "ACCESS1-0")

    def test_esgf_search_uses_bounded_dataset_query(self):
        from io import BytesIO
        from urllib.parse import parse_qs, urlparse

        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()

        with patch("esmval_gui.esgf.urlopen", return_value=Response(b'{"response":{"docs":[],"numFound":0}}')) as mock:
            self.assertEqual(search_datasets("ACCESS", "CMIP6", experiment="historical", variable="tas",
                                             ensemble="r1i1p1f1")["results"], [])
        url = mock.call_args.args[0].full_url
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params["query"], ["source_id:ACCESS*"])
        self.assertEqual(params["latest"], ["true"])
        self.assertNotIn("replica", params)
        self.assertEqual(params["experiment_id"], ["historical"])
        self.assertEqual(params["variable"], ["tas"])
        self.assertEqual(params["member_id"], ["r1i1p1f1"])
        with self.assertRaises(ValueError):
            search_datasets("*:*", "CMIP6")

    def test_new_recipe_builder_creates_editable_starter(self):
        values = dict(title="Global temperature", description="Compare model temperature.", author="Example Scientist",
                      filename="recipe_global_temperature.yml", dataset="ACCESS-ESM1-5", project="CMIP6",
                      exp="historical", diagnostic="maps", variable="model_tas", short_name="tas", mip="Amon",
                      realm="atmos", script_path="examples/diagnostic.py")
        source = build_recipe(**values)
        recipe = parse_recipe(source)
        self.assertEqual(recipe["documentation"]["authors"], ["Example Scientist"])
        self.assertEqual(recipe["diagnostics"]["maps"]["variables"]["model_tas"]["short_name"], "tas")
        self.assertEqual(recipe["diagnostics"]["maps"]["scripts"]["plot"]["script"], "examples/diagnostic.py")
        self.assertEqual(summarize(source)["messages"], [])
        self.assertEqual(summarize(source)["counts"], {"datasets": 1, "profiles": 0, "variables": 1, "diagnostics": 1})
        source = create_profile(source, "annual_mean")
        self.assertIn("annual_mean", parse_recipe(source)["preprocessors"])
        draft = build_recipe(**{**values, "script_path": ""})
        self.assertEqual(parse_recipe(draft)["diagnostics"]["maps"]["scripts"], {})
        draft = add_script(draft, "maps", "plot", "examples/diagnostic.py")
        self.assertEqual(parse_recipe(draft)["diagnostics"]["maps"]["scripts"]["plot"]["script"], "examples/diagnostic.py")
        with self.assertRaisesRegex(ValueError, "already in use"):
            add_script(draft, "maps", "plot", "examples/other.py")
        with self.assertRaisesRegex(ValueError, "filename"):
            build_recipe(**{**values, "filename": "../other.yml"})

    def test_library_realms_follow_explicit_diagnostic_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "recipe_mixed.yml").write_text("""diagnostics:
  first: {realms: [atmos, ocean]}
  second: {realms: [seaIce, atmos]}
""")
            (root / "recipe_untagged.yml").write_text("diagnostics: {first: {variables: {}}}\n")
            items = {item["name"]: item for item in list_recipes(root)}
            self.assertEqual(items["recipe_mixed.yml"]["realms"], ["atmos", "ocean", "seaIce"])
            self.assertEqual(items["recipe_untagged.yml"]["realms"], [])

    def test_create_pipeline_components_and_dataset_scopes(self):
        source = "# keep header\ndocumentation: {title: Creation}\n"
        source, diagnostic_path = create_node(source, "diagnostic", "maps", script_name="plot",
                                              script_path="examples/diagnostic.py")
        source, profile_path = create_node(source, "profile", "annual_mean")
        source, variable_path = create_node(source, "variable", "cmip6", diagnostic="maps",
                                           short_name="tas", profile="annual_mean")
        source, global_path = create_node(source, "dataset", "ModelA", project="CMIP6", exp="historical")
        source, diagnostic_dataset_path = create_node(source, "dataset", "ModelB", scope="diagnostic",
                                                      diagnostic="maps", project="CMIP6")
        source, variable_dataset_path = create_node(source, "dataset", "ModelC", scope="variable",
                                                    diagnostic="maps", variable="cmip6", project="CMIP6")
        recipe = parse_recipe(source)
        self.assertIn("# keep header", source)
        self.assertEqual(diagnostic_path, ["diagnostics", "maps"])
        self.assertEqual(profile_path, ["preprocessors", "annual_mean"])
        self.assertEqual(variable_path, ["diagnostics", "maps", "variables", "cmip6"])
        self.assertEqual(global_path, ["datasets", 0])
        self.assertEqual(diagnostic_dataset_path, ["diagnostics", "maps", "additional_datasets", 0])
        self.assertEqual(variable_dataset_path, ["diagnostics", "maps", "variables", "cmip6", "additional_datasets", 0])
        self.assertEqual(recipe["diagnostics"]["maps"]["scripts"]["plot"]["script"], "examples/diagnostic.py")
        self.assertEqual(recipe["diagnostics"]["maps"]["variables"]["cmip6"]["short_name"], "tas")
        self.assertEqual(summarize(source)["counts"], {"datasets": 3, "profiles": 1, "variables": 1, "diagnostics": 1})
        with self.assertRaisesRegex(ValueError, "already in use"):
            create_node(source, "diagnostic", "maps")
        with self.assertRaisesRegex(ValueError, "existing diagnostic"):
            create_node(source, "variable", "pr", diagnostic="missing")

    def test_recipe_context_resolves_people_references_and_documented_figure(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            root = project / "esmvaltool" / "recipes"
            root.mkdir(parents=True)
            (root / "recipe_demo.yml").write_text("""documentation:
  title: Demo
  description: A map of temperature.
  authors: [scientist_one]
  maintainer: [scientist_one]
  references: [paper_one]
  projects: [project_one]
diagnostics: {}
""")
            (root.parent / "config-references.yml").write_text("""authors:
  scientist_one: {name: 'One, Scientist', institute: Example Institute}
projects: {project_one: Climate project}
""")
            references = root.parent / "references"
            references.mkdir()
            (references / "paper_one.bibtex").write_text("@article{paper_one,\n  title = {A paper about maps},\n  doi = {10.1234/demo}\n}\n")
            docs = project / "doc" / "sphinx" / "source" / "recipes"
            (docs / "figures").mkdir(parents=True)
            (docs / "figures" / "map.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (docs / "recipe_demo.rst").write_text("""Demo
====
Overview
--------
The recipe compares model temperature on a map.

.. figure:: /recipes/figures/map.png

   A temperature map from this recipe.
""")
            summary = summarize(read_library_recipe(root, "recipe_demo.yml"), root)
            self.assertEqual(summary["documentation"]["authors"][0]["name"], "One, Scientist")
            self.assertEqual(summary["documentation"]["references"][0]["url"], "https://doi.org/10.1234/demo")
            self.assertEqual(summary["documentation"]["projects"][0]["name"], "Climate project")
            info = library_recipe_info(root, "recipe_demo.yml")
            self.assertTrue(info["docs_url"].endswith("/recipes/recipe_demo.html"))
            self.assertEqual(info["overview"], "The recipe compares model temperature on a map.")
            self.assertEqual(info["figure"]["caption"], "A temperature map from this recipe.")
            self.assertEqual(info["figure"]["scope"], "recipe")

    def test_library_collection_parses_without_losing_source(self):
        root = available_recipe_root()
        if root is None:
            self.skipTest("No local ESMValTool recipe collection")
        for item in list_recipes(root):
            with self.subTest(item=item["path"]):
                source = read_library_recipe(root, item["path"])
                summary = summarize(source)
                self.assertIn("graph", summary)
                self.assertEqual(source, read_library_recipe(root, item["path"]))

    def test_catalogue_covers_configurable_collection_steps(self):
        catalogue = get_catalogue()
        names = {brick["name"] for brick in catalogue["bricks"]}
        self.assertGreater(len(names), 50)
        self.assertTrue(all(brick["documentation"] for brick in catalogue["bricks"]))
        self.assertNotIn("load", names)
        self.assertNotIn("save", names)
        root = available_recipe_root()
        if root is None:
            return
        for item in list_recipes(root):
            summary = summarize(read_library_recipe(root, item["path"]))
            self.assertTrue(all(step["name"] in names for profile in summary["graph"]["profiles"]
                                for step in profile["steps"]), item["path"])

    def test_profile_edit_preserves_comments(self):
        source = "# recipe comment\ndocumentation: {title: Demo}\npreprocessors:\n  simple: # profile comment\n    convert_units: {units: K}\ndiagnostics: {}\n"
        edited = edit_profile(source, "simple", "convert_units", "units: degrees_C")
        self.assertIn("# recipe comment", edited)
        self.assertIn("# profile comment", edited)
        self.assertEqual(parse_recipe(edited)["preprocessors"]["simple"]["convert_units"]["units"], "degrees_C")

    def test_library_paths_cannot_escape_root(self):
        root = Path(tempfile.mkdtemp())
        with self.assertRaises(FileNotFoundError):
            read_library_recipe(root, "../other.yml")

    def test_builder_round_trip_and_variable_assignment(self):
        source = "# header\ndocumentation: {title: Demo}\ndiagnostics:\n  map:\n    variables:\n      tas: {project: CMIP6}\n    scripts: {}\n"
        source = create_profile(source, "my_profile")
        source = edit_profile_fields(source, "my_profile", "regrid",
                                     {"target_grid": "1x1", "scheme": "linear", "lat_offset": "false"})
        source = edit_profile_fields(source, "my_profile", "convert_units", {"units": "degrees_C"})
        source = set_profile_order(source, "my_profile", True)
        source = move_profile_step(source, "my_profile", "convert_units", -1)
        source = set_variable_profile(source, "map", "tas", "my_profile")
        parsed = parse_recipe(source)
        self.assertIn("# header", source)
        self.assertEqual(list(parsed["preprocessors"]["my_profile"])[:2], ["convert_units", "regrid"])
        self.assertIs(parsed["preprocessors"]["my_profile"]["regrid"]["lat_offset"], False)
        self.assertEqual(parsed["diagnostics"]["map"]["variables"]["tas"]["preprocessor"], "my_profile")
        self.assertEqual(summarize(source)["counts"]["variables"], 1)

    def test_dataset_links_follow_recipe_diagnostic_and_variable_scopes(self):
        source = """documentation: {title: Dataset links}
datasets:
  - {dataset: ModelA, project: CMIP6}
diagnostics:
  maps:
    additional_datasets:
      - {dataset: ModelB, project: CMIP6}
      - {dataset: ModelA, project: CMIP6}
    variables:
      tas:
        additional_datasets: [{dataset: ModelC, project: CMIP6}]
      pr: {}
    scripts: {}
"""
        graph = summarize(source)["graph"]
        datasets = {item["label"]: item for item in graph["datasets"]}
        variables = {item["name"]: item for item in graph["variables"]}
        self.assertEqual(len(datasets), 3)
        self.assertEqual(datasets["ModelA"]["definition_count"], 2)
        self.assertEqual(set(variables["tas"]["dataset_ids"]), {item["id"] for item in datasets.values()})
        self.assertEqual(set(variables["pr"]["dataset_ids"]), {datasets["ModelA"]["id"], datasets["ModelB"]["id"]})

    def test_variable_group_displays_short_name_without_changing_edit_key(self):
        source = """documentation: {title: Grouped variable}
preprocessors:
  prep_zonal: {area_statistics: {operator: mean}}
diagnostics:
  zonal:
    variables:
      cmip5: {short_name: tos, preprocessor: prep_zonal}
      cmip6: {short_name: tos, preprocessor: prep_zonal}
      tas: {}
    scripts: {}
"""
        graph = summarize(source)["graph"]
        variables = {item["name"]: item for item in graph["variables"]}
        self.assertEqual(variables["cmip5"]["short_name"], "tos")
        self.assertEqual(variables["cmip6"]["short_name"], "tos")
        self.assertEqual(variables["tas"]["short_name"], "tas")
        self.assertEqual(graph["diagnostics"][0]["variables"][0]["short_name"], "tos")
        edited = set_variable_profile(source, "zonal", "cmip6", "default")
        grouped = parse_recipe(edited)["diagnostics"]["zonal"]["variables"]
        self.assertEqual(grouped["cmip5"]["preprocessor"], "prep_zonal")
        self.assertNotIn("preprocessor", grouped["cmip6"])

    def test_pipeline_node_edits_target_one_dataset_and_update_references(self):
        source = """# keep this header
documentation: {title: Edit nodes}
datasets:
  - {dataset: ModelA, project: CMIP6}
preprocessors:
  mean: {area_statistics: {operator: mean}}
diagnostics:
  maps:
    variables:
      cmip6:
        short_name: tas
        preprocessor: mean
        additional_datasets:
          - {dataset: ModelA, project: CMIP6, exp: historical}
    scripts: {plot: {script: examples/diagnostic.py}}
  child:
    ancestors: [maps/plot]
    variables: {}
    scripts: {}
"""
        dataset = next(item for item in summarize(source)["graph"]["datasets"] if item["label"] == "ModelA")
        self.assertEqual(len(dataset["occurrences"]), 2)
        path = dataset["occurrences"][1]["path"]
        self.assertIn("exp: historical", read_node(source, "dataset", path)["definition"])
        source, _ = edit_node(source, "dataset", path, "dataset: ModelB\nproject: CMIP6\nexp: historical\n")
        parsed = parse_recipe(source)
        self.assertEqual(parsed["datasets"][0]["dataset"], "ModelA")
        self.assertEqual(parsed["diagnostics"]["maps"]["variables"]["cmip6"]["additional_datasets"][0]["dataset"], "ModelB")
        source, variable_path = edit_node(source, "variable", ["diagnostics", "maps", "variables", "cmip6"],
                                          "short_name: pr\npreprocessor: mean\n", "rainfall")
        self.assertEqual(variable_path[-1], "rainfall")
        source, profile_path = edit_node(source, "profile", ["preprocessors", "mean"],
                                         "area_statistics: {operator: mean}\n", "climate_mean")
        self.assertEqual(profile_path[-1], "climate_mean")
        source, diagnostic_path = edit_node(source, "diagnostic", ["diagnostics", "maps"],
                                            read_node(source, "diagnostic", ["diagnostics", "maps"])["definition"],
                                            "maps_new")
        self.assertEqual(diagnostic_path[-1], "maps_new")
        parsed = parse_recipe(source)
        self.assertEqual(parsed["diagnostics"]["maps_new"]["variables"]["rainfall"]["preprocessor"], "climate_mean")
        self.assertEqual(parsed["diagnostics"]["child"]["ancestors"], ["maps_new/plot"])
        self.assertIn("# keep this header", source)

    def test_node_editor_rejects_invalid_yaml_and_path(self):
        source = "documentation: {title: Demo}\ndiagnostics: {maps: {variables: {tas: {}}, scripts: {}}, other: {variables: {}, scripts: {}}}\n"
        with self.assertRaisesRegex(ValueError, "Invalid recipe node path"):
            read_node(source, "variable", ["diagnostics", "maps"])
        with self.assertRaisesRegex(ValueError, "YAML mapping"):
            edit_node(source, "variable", ["diagnostics", "maps", "variables", "tas"], "[1, 2]")
        with self.assertRaisesRegex(ValueError, "already in use"):
            edit_node(source, "diagnostic", ["diagnostics", "maps"], "variables: {}\nscripts: {}\n", "other")

    def test_dataset_edit_detaches_shared_yaml_anchor(self):
        source = """documentation: {title: Anchored datasets}
shared: &models
  - {dataset: ModelA, project: CMIP6}
diagnostics:
  maps:
    variables:
      tas: {additional_datasets: *models}
      pr: {additional_datasets: *models}
    scripts: {}
"""
        path = ["diagnostics", "maps", "variables", "pr", "additional_datasets", 0]
        edited, _ = edit_node(source, "dataset", path, "dataset: ModelB\nproject: CMIP6\n")
        variables = parse_recipe(edited)["diagnostics"]["maps"]["variables"]
        self.assertEqual(variables["tas"]["additional_datasets"][0]["dataset"], "ModelA")
        self.assertEqual(variables["pr"]["additional_datasets"][0]["dataset"], "ModelB")

    def test_scalar_field_edit_keeps_nested_variable_settings(self):
        source = """# recipe header
documentation: {title: Fields}
diagnostics:
  maps:
    variables:
      cmip6:
        short_name: tas # keep inline comment
        start_year: 2000
        additional_datasets: [{dataset: ModelA, project: CMIP6}]
    scripts: {}
"""
        path = ["diagnostics", "maps", "variables", "cmip6"]
        edited, _ = edit_node_fields(source, "variable", path,
                                     {"short_name": "pr", "start_year": "2001", "end_year": "2010"}, [], None)
        variable = parse_recipe(edited)["diagnostics"]["maps"]["variables"]["cmip6"]
        self.assertEqual(variable["short_name"], "pr")
        self.assertEqual(variable["start_year"], 2001)
        self.assertEqual(variable["end_year"], 2010)
        self.assertEqual(variable["additional_datasets"][0]["dataset"], "ModelA")
        self.assertIn("# recipe header", edited)
        self.assertIn("# keep inline comment", edited)


class RemoteTests(unittest.TestCase):
    def settings(self):
        return RemoteSettings(project="ab12", work_dir="/scratch/ab12/exampleuser/esmval-gui-jobs", storage="scratch/ab12+gdata/cd34")

    def test_script_uses_bounded_pbs_resources(self):
        script = script_for(self.settings(), "/scratch/ab12/exampleuser/esmval-gui-jobs/1/recipe.yml", "/scratch/ab12/exampleuser/esmval-gui-jobs/1")
        self.assertIn("#PBS -P ab12", script)
        self.assertIn("#PBS -l storage=scratch/ab12+gdata/cd34", script)
        self.assertIn('export ESMFMKFILE="$esmval_prefix/lib/esmf.mk"', script)
        self.assertIn("esmvaltool run", script)

    def test_legacy_config_file_flag(self):
        settings = self.settings().model_copy(update={"config_file": "/scratch/ab12/user/config-user.yml"})
        script = script_for(settings, "/scratch/ab12/user/recipe.yml", "/scratch/ab12/user")
        self.assertIn("esmvaltool run --config_file /scratch/ab12/user/config-user.yml", script)

    def test_directive_injection_is_rejected(self):
        with self.assertRaises(ValidationError):
            RemoteSettings(project="ab12\n#PBS -q express", work_dir="/scratch/ab12/user")
        with self.assertRaises(ValidationError):
            RemoteSettings(project="ab12", work_dir="/scratch/ab12/user\n#PBS -q express")

    @patch("esmval_gui.remote.ssh")
    def test_submit_uploads_recipe_and_script_then_qsub(self, ssh):
        ssh.side_effect = ["", "", "", "12345.gadi-pbs"]
        with tempfile.TemporaryDirectory() as tmp:
            job = submit(self.settings(), "documentation: {}\ndiagnostics: {}\n", Path(tmp))
            self.assertEqual(job["job_id"], "12345.gadi-pbs")
            self.assertEqual(ssh.call_count, 4)
            self.assertIn("documentation:", ssh.call_args_list[1].args[2])
            self.assertTrue((Path(tmp) / f"{job['id']}.json").exists())

    @patch("esmval_gui.remote.status", return_value={"state": "queued"})
    @patch("esmval_gui.remote.ssh")
    def test_cancel_marks_job_history(self, ssh, _status):
        with tempfile.TemporaryDirectory() as tmp:
            record = {"id": "abcdef123456", "job_id": "12345.gadi-pbs", "host": "gadi"}
            result = cancel(Path(tmp), record)
            self.assertEqual(result["state"], "cancelled")
            self.assertEqual(ssh.call_args.args[1], "qdel 12345.gadi-pbs")
            self.assertTrue(record["cancelled"])


if __name__ == "__main__":
    unittest.main()
