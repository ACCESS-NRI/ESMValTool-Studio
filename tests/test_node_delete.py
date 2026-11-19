import unittest

from fastapi import HTTPException

from esmval_gui.app import NodeDelete, delete_node as delete_endpoint
from esmval_gui.recipes import delete_node, parse_recipe, summarize


SOURCE = '''# Preserve recipe comments
documentation: {title: Deletion test}
datasets:
  - {dataset: ModelA, project: CMIP6, exp: historical}
  - {dataset: ModelB, project: CMIP6}
  - {dataset: ModelA, project: CMIP6, exp: ssp585}
preprocessors:
  mean: {area_statistics: {operator: mean}}
  other: {}
diagnostics:
  maps:
    additional_datasets: [{dataset: ModelA, project: CMIP6}]
    variables:
      tas: {preprocessor: mean, additional_datasets: [{dataset: ModelA, project: CMIP6}]}
      pr: {preprocessor: other}
    scripts: {plot: {script: examples/diagnostic.py}}
  child:
    ancestors: [maps/plot, maps, maps_other/plot]
    variables: {tos: {preprocessor: mean}}
    scripts:
      plot: {script: examples/diagnostic.py, ancestors: [maps/plot, '*']}
  maps_other:
    variables: {}
    scripts: {plot: {script: examples/diagnostic.py}}
'''


class NodeDeletionTests(unittest.TestCase):
    def test_dataset_definition_and_group(self):
        one = parse_recipe(delete_node(SOURCE, 'dataset', ['datasets', 0]))
        self.assertEqual([d['dataset'] for d in one['datasets']], ['ModelB', 'ModelA'])
        self.assertEqual(len(one['diagnostics']['maps']['additional_datasets']), 1)
        source = delete_node(SOURCE, 'dataset', ['datasets', 0], True)
        result = parse_recipe(source)
        self.assertEqual([d['dataset'] for d in result['datasets']], ['ModelB'])
        self.assertEqual(result['diagnostics']['maps']['additional_datasets'], [])
        self.assertEqual(result['diagnostics']['maps']['variables']['tas']['additional_datasets'], [])
        self.assertEqual(summarize(source)['counts']['datasets'], 1)
        self.assertIn('# Preserve recipe comments', source)

    def test_dataset_delete_detaches_shared_list(self):
        source = '''documentation: {title: Shared}
datasets: &models [{dataset: A}, {dataset: B}]
diagnostics:
  maps:
    additional_datasets: *models
    variables: {tas: {additional_datasets: *models}}
    scripts: {}
'''
        result = parse_recipe(delete_node(source, 'dataset', ['diagnostics', 'maps', 'additional_datasets', 0]))
        self.assertEqual(len(result['datasets']), 2)
        self.assertEqual(len(result['diagnostics']['maps']['variables']['tas']['additional_datasets']), 2)
        self.assertEqual(result['diagnostics']['maps']['additional_datasets'][0]['dataset'], 'B')
        result = parse_recipe(delete_node(source, 'dataset', ['datasets', 0], True))
        self.assertEqual(result['datasets'], [{'dataset': 'B'}])
        self.assertEqual(result['diagnostics']['maps']['additional_datasets'], [{'dataset': 'B'}])
        self.assertEqual(result['diagnostics']['maps']['variables']['tas']['additional_datasets'], [{'dataset': 'B'}])

    def test_profile_removes_assignments_only_for_deleted_profile(self):
        result = parse_recipe(delete_node(SOURCE, 'profile', ['preprocessors', 'mean']))
        self.assertEqual(list(result['preprocessors']), ['other'])
        self.assertNotIn('preprocessor', result['diagnostics']['maps']['variables']['tas'])
        self.assertNotIn('preprocessor', result['diagnostics']['child']['variables']['tos'])
        self.assertEqual(result['diagnostics']['maps']['variables']['pr']['preprocessor'], 'other')

    def test_variable_preserves_siblings_and_detaches_anchors(self):
        source = '''diagnostics:
  maps:
    variables: &variables {tas: {}, pr: {}}
    scripts: {}
  child:
    variables: *variables
    scripts: {}
'''
        result = parse_recipe(delete_node(source, 'variable', ['diagnostics', 'maps', 'variables', 'tas']))
        self.assertEqual(list(result['diagnostics']['maps']['variables']), ['pr'])
        self.assertEqual(list(result['diagnostics']['child']['variables']), ['tas', 'pr'])

    def test_profile_fallback_overrides_inherited_yaml_assignment(self):
        source = '''documentation: {title: Inherited profile}
preprocessors: {mean: {}}
diagnostics:
  maps:
    variables:
      tas: &base {preprocessor: mean}
      pr: {<<: *base, short_name: pr}
    scripts: {}
'''
        result = delete_node(source, 'profile', ['preprocessors', 'mean'])
        self.assertEqual([item['profile'] for item in summarize(result)['graph']['variables']],
                         ['default', 'default'])
        self.assertEqual(summarize(result)['messages'], [])

    def test_diagnostic_removes_children_and_exact_ancestor_references(self):
        result = parse_recipe(delete_node(SOURCE, 'diagnostic', ['diagnostics', 'maps']))
        self.assertNotIn('maps', result['diagnostics'])
        child = result['diagnostics']['child']
        self.assertEqual(child['ancestors'], ['maps_other/plot'])
        self.assertEqual(child['scripts']['plot']['ancestors'], ['*'])
        self.assertIn('mean', result['preprocessors'])

    def test_script_removes_only_its_exact_references(self):
        result = parse_recipe(delete_node(SOURCE, 'script', ['diagnostics', 'maps', 'scripts', 'plot']))
        self.assertEqual(result['diagnostics']['maps']['scripts'], {})
        self.assertEqual(result['diagnostics']['child']['ancestors'], ['maps', 'maps_other/plot'])
        self.assertEqual(result['diagnostics']['child']['scripts']['plot']['ancestors'], ['*'])
        self.assertIn('tas', result['diagnostics']['maps']['variables'])

    def test_last_elements_leave_editable_recipe(self):
        source = 'datasets: [{dataset: A}]\npreprocessors: {mean: {}}\ndiagnostics: {maps: {variables: {tas: {}}, scripts: {}}}\n'
        for kind, path in [('dataset', ['datasets', 0]), ('profile', ['preprocessors', 'mean']),
                           ('variable', ['diagnostics', 'maps', 'variables', 'tas']),
                           ('diagnostic', ['diagnostics', 'maps'])]:
            source = delete_node(source, kind, path)
        self.assertEqual(summarize(source)['counts'], {'datasets': 0, 'profiles': 0, 'variables': 0, 'diagnostics': 0})

    def test_endpoint_returns_updated_summary_and_rejects_invalid_targets(self):
        result = delete_endpoint(NodeDelete(yaml=SOURCE, kind='dataset', path=['datasets', 0], all_occurrences=True))
        self.assertEqual(result['summary']['counts']['datasets'], 1)
        for kind, path in [('dataset', ['datasets', -1]), ('dataset', ['datasets', 99]),
                           ('variable', ['diagnostics', 'maps']), ('profile', ['preprocessors', 'default']),
                           ('diagnostic', ['documentation'])]:
            with self.subTest(kind=kind, path=path), self.assertRaises(HTTPException) as error:
                delete_endpoint(NodeDelete(yaml=SOURCE, kind=kind, path=path))
            self.assertEqual(error.exception.status_code, 400)
        with self.assertRaisesRegex(ValueError, 'Only datasets'):
            delete_node(SOURCE, 'profile', ['preprocessors', 'mean'], True)


if __name__ == '__main__':
    unittest.main()
