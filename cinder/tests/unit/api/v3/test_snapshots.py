# Copyright 2016 EMC Corporation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import ddt

import mock

from cinder.api.openstack import api_version_request as api_version
from cinder.api.v3 import snapshots
from cinder import context
from cinder import exception
from cinder.objects import fields
from cinder import test
from cinder.tests.unit.api import fakes
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import fake_snapshot
from cinder.tests.unit import fake_volume
from cinder import volume

UUID = '00000000-0000-0000-0000-000000000001'
INVALID_UUID = '00000000-0000-0000-0000-000000000002'


def stub_get(self, context, *args, **kwargs):
    vol = {'id': fake.VOLUME_ID,
           'size': 100,
           'name': 'fake',
           'host': 'fake-host',
           'status': 'available',
           'encryption_key_id': None,
           'volume_type_id': None,
           'migration_status': None,
           'availability_zone': 'fake-zone',
           'attach_status': 'detached',
           'metadata': {}}
    return fake_volume.fake_volume_obj(context, **vol)


def create_snapshot_query_with_metadata(metadata_query_string,
                                        api_microversion):
    """Helper to create metadata querystring with microversion"""
    req = fakes.HTTPRequest.blank('/v3/snapshots?metadata=' +
                                  metadata_query_string)
    req.headers["OpenStack-API-Version"] = "volume " + api_microversion
    req.api_version_request = api_version.APIVersionRequest(
        api_microversion)

    return req


@ddt.ddt
class SnapshotApiTest(test.TestCase):
    def setUp(self):
        super(SnapshotApiTest, self).setUp()
        self.stubs.Set(volume.api.API, 'get', stub_get)
        self.controller = snapshots.SnapshotsController()
        self.ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID, True)

    @ddt.data('3.14', '3.13')
    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.objects.Snapshot.get_by_id')
    def test_snapshot_show(self, max_ver, snapshot_get_by_id, volume_get_by_id,
                           snapshot_metadata_get):
        snapshot = {
            'id': UUID,
            'volume_id': fake.VOLUME_ID,
            'status': fields.SnapshotStatus.AVAILABLE,
            'volume_size': 100,
            'display_name': 'Default name',
            'display_description': 'Default description',
            'expected_attrs': ['metadata'],
            'group_snapshot_id': None,
        }
        ctx = context.RequestContext(fake.PROJECT_ID, fake.USER_ID, True)
        snapshot_obj = fake_snapshot.fake_snapshot_obj(ctx, **snapshot)
        fake_volume_obj = fake_volume.fake_volume_obj(ctx)
        snapshot_get_by_id.return_value = snapshot_obj
        volume_get_by_id.return_value = fake_volume_obj
        req = fakes.HTTPRequest.blank('/v3/snapshots/%s' % UUID)
        req.api_version_request = api_version.APIVersionRequest(max_ver)
        resp_dict = self.controller.show(req, UUID)

        self.assertIn('snapshot', resp_dict)
        self.assertEqual(UUID, resp_dict['snapshot']['id'])
        self.assertIn('updated_at', resp_dict['snapshot'])
        if max_ver == '3.14':
            self.assertIn('group_snapshot_id', resp_dict['snapshot'])
        elif max_ver == '3.13':
            self.assertNotIn('group_snapshot_id', resp_dict['snapshot'])

    def test_snapshot_show_invalid_id(self):
        snapshot_id = INVALID_UUID
        req = fakes.HTTPRequest.blank('/v3/snapshots/%s' % snapshot_id)
        self.assertRaises(exception.SnapshotNotFound,
                          self.controller.show, req, snapshot_id)

    def _create_snapshot_with_metadata(self, metadata):
        """Creates test snapshopt with provided metadata"""
        req = fakes.HTTPRequest.blank('/v3/snapshots')
        snap = {"volume_size": 200,
                "volume_id": fake.VOLUME_ID,
                "display_name": "Volume Test Name",
                "display_description": "Volume Test Desc",
                "availability_zone": "zone1:host1",
                "host": "fake-host",
                "metadata": metadata}
        body = {"snapshot": snap}
        self.controller.create(req, body)

    def test_snapshot_list_with_one_metadata_in_filter(self):
        # Create snapshot with metadata key1: value1
        metadata = {"key1": "val1"}
        self._create_snapshot_with_metadata(metadata)

        # Create request with metadata filter key1: value1
        req = create_snapshot_query_with_metadata('{"key1":"val1"}', '3.22')

        # query controller with above request
        res_dict = self.controller.detail(req)

        # verify 1 snapshot is returned
        self.assertEqual(1, len(res_dict['snapshots']))

        # verify if the medadata of the returned snapshot is key1: value1
        self.assertDictEqual({"key1": "val1"}, res_dict['snapshots'][0][
            'metadata'])

        # Create request with metadata filter key2: value2
        req = create_snapshot_query_with_metadata('{"key2":"val2"}', '3.22')

        # query controller with above request
        res_dict = self.controller.detail(req)

        # verify no snapshot is returned
        self.assertEqual(0, len(res_dict['snapshots']))

    def test_snapshot_list_with_multiple_metadata_in_filter(self):
        # Create snapshot with metadata key1: value1, key11: value11
        metadata = {"key1": "val1", "key11": "val11"}
        self._create_snapshot_with_metadata(metadata)

        # Create request with metadata filter key1: value1, key11: value11
        req = create_snapshot_query_with_metadata(
            '{"key1":"val1", "key11":"val11"}', '3.22')

        # query controller with above request
        res_dict = self.controller.detail(req)

        # verify 1 snapshot is returned
        self.assertEqual(1, len(res_dict['snapshots']))

        # verify if the medadata of the returned snapshot is key1: value1
        self.assertDictEqual({"key1": "val1", "key11": "val11"}, res_dict[
            'snapshots'][0]['metadata'])

        # Create request with metadata filter key1: value1
        req = create_snapshot_query_with_metadata('{"key1":"val1"}', '3.22')

        # query controller with above request
        res_dict = self.controller.detail(req)

        # verify 1 snapshot is returned
        self.assertEqual(1, len(res_dict['snapshots']))

        # verify if the medadata of the returned snapshot is key1: value1
        self.assertDictEqual({"key1": "val1", "key11": "val11"}, res_dict[
            'snapshots'][0]['metadata'])

    def test_snapshot_list_with_metadata_unsupported_microversion(self):
        # Create snapshot with metadata key1: value1
        metadata = {"key1": "val1"}
        self._create_snapshot_with_metadata(metadata)

        # Create request with metadata filter key2: value2
        req = create_snapshot_query_with_metadata('{"key2":"val2"}', '3.21')

        # query controller with above request
        res_dict = self.controller.detail(req)

        # verify some snapshot is returned
        self.assertNotEqual(0, len(res_dict['snapshots']))
