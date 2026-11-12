"""Catalogue scale/filter contracts and non-billable historical migration."""
import unittest
from phase2.catalog import select_catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.items = [{'template_code': str(i), 'actor_count': i % 3 + 1,
                       'location_association': '漓江' if i % 2 else '银子岩',
                       'relationship_tags': ['夫妻'] if i % 3 == 1 else ['朋友'],
                       'video_genre': '旅拍'} for i in range(503)]

    def test_503_templates_paginate_without_duplicates_and_resume(self):
        seen, offset = [], 0
        while offset is not None:
            result = select_catalog(self.items, {'offset': offset})
            self.assertLessEqual(len(result['items']), 8)
            seen.extend(i['template_code'] for i in result['items'])
            offset = result['next_offset']
        self.assertEqual(len(seen), 503)
        self.assertEqual(len(set(seen)), 503)
        resumed = select_catalog(list(reversed(self.items)), {'offset': 16})
        self.assertEqual([i['template_code'] for i in resumed['items']], seen[16:24])
        legacy_random = select_catalog(self.items, {'seed': 'visitor-b', 'random': '1'})
        self.assertEqual([i['template_code'] for i in legacy_random['items']], seen[:8])

    def test_manual_heat_is_the_only_ranking_signal(self):
        heat = {'0': 50, '1': 90, '2': 50}
        result = select_catalog(self.items[:4], {}, heat)
        self.assertEqual([i['template_code'] for i in result['items']], ['1', '0', '2', '3'])

    def test_filters_never_fall_back_to_wrong_roles(self):
        result = select_catalog(self.items, {'actor_count': '2', 'scene': '漓江', 'relationship': '夫妻'})
        self.assertGreater(result['total'], 0)
        self.assertTrue(all(i['actor_count'] == 2 and i['location_association'] == '漓江' for i in result['items']))
        self.assertFalse(select_catalog(self.items, {'actor_count': '1', 'relationship': '夫妻'})['items'])
        self.assertEqual(set(result['facets']), {'scene','actor_count','gender','relationship'})

    def test_invalid_pagination_is_rejected(self):
        for query in ({'limit': 0}, {'limit': 31}, {'offset': -1}, {'offset': 'x'}):
            with self.assertRaises(ValueError):
                select_catalog(self.items, query)

    def test_removed_filters_cannot_hide_templates_from_old_clients(self):
        self.items[0]['age_tags'] = ['青年']
        self.assertEqual(select_catalog(self.items, {'age': '青年','video_genre':'old'})['total'], 503)
        self.assertEqual(select_catalog(self.items, {'lead_gender': '男'})['total'], 503)

    def test_later_relationship_does_not_hide_other_count_choices(self):
        result = select_catalog(self.items, {'actor_count': '2', 'relationship': '夫妻'})
        self.assertEqual(result['facets']['actor_count'], ['1','2','3'])
        self.assertTrue(all(i['actor_count'] == 2 for i in result['items']))


if __name__=='__main__': unittest.main()
