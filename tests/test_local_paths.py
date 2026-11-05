import os
import tempfile
import unittest
from pathlib import Path

from esmval_gui.recipes import list_recipes, read_library_recipe
from esmval_gui.scripts_catalogue import read_source, search


class LocalPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "source" / "esmvaltool" / "recipes"
        self.root.mkdir(parents=True)
        self.scripts = self.root.parent / "diag_scripts"
        self.scripts.mkdir()

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as exc:
            if os.name == "nt" and exc.winerror == 1314:
                self.skipTest("Windows account cannot create symbolic links")
            raise

    def alias_root(self):
        alias = self.directory / "alias"
        self.symlink(alias, self.directory / "source", directory=True)
        return alias / "esmvaltool" / "recipes"

    def test_recipe_library_accepts_symlinked_parent_and_rejects_escape(self):
        source = "documentation: {title: Demo}\n"
        (self.root / "recipe_demo.yml").write_text(source, encoding="utf-8")
        outside = self.root.parent / "outside.yml"
        outside.write_text(source, encoding="utf-8")
        self.symlink(self.root / "recipe_link.yml", outside)
        root = self.alias_root()

        self.assertEqual(read_library_recipe(root, "recipe_demo.yml"), source)
        for relative in ("../outside.yml", "recipe_link.yml"):
            with self.subTest(relative=relative), self.assertRaises(FileNotFoundError):
                read_library_recipe(root, relative)

    def test_script_catalogue_accepts_symlinked_parent_and_rejects_escape(self):
        (self.scripts / "plot.py").write_text('"""Example plot."""\n', encoding="utf-8")
        outside = self.scripts.parent / "outside.py"
        outside.write_text('"""Outside script."""\n', encoding="utf-8")
        self.symlink(self.scripts / "linked.py", outside)
        (self.root / "recipe_demo.yml").write_text(
            "diagnostics:\n  demo:\n    scripts:\n"
            "      plot: {script: plot.py}\n"
            "      outside: {script: ../outside.py}\n"
            "      linked: {script: linked.py}\n", encoding="utf-8")

        result = search(self.alias_root())
        self.assertEqual(result["scripts"], [
            {"path": "plot.py", "summary": "Example plot.", "used_by": 1}])

    def test_script_source_normalizes_line_endings(self):
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=repr(newline)):
                source = f"# Température{newline}print('example'){newline}"
                (self.scripts / "plot.py").write_bytes(source.encode("utf-8"))
                result = read_source(self.root, "plot.py")
                self.assertEqual(result["source"], "# Température\nprint('example')\n")
                self.assertEqual(result["line_count"], 2)

    def test_nested_recipe_paths_use_forward_slashes(self):
        nested = self.root / "examples" / "nested"
        nested.mkdir(parents=True)
        (nested / "recipe_demo.yml").write_text("diagnostics: {}\n", encoding="utf-8")
        entry = list_recipes(self.root)[0]
        self.assertEqual(entry["path"], "examples/nested/recipe_demo.yml")
        self.assertEqual(entry["group"], "examples/nested")


if __name__ == "__main__":
    unittest.main()
