#   Copyright (c) 2017 Mirantis Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.

""" Tests for manage_existing_snapshot TaskFlow."""

# TODO(mdovgal): add tests for other TaskFlow cases

import mock

from cinder import context
from cinder import test
from cinder.tests.unit import fake_snapshot
from cinder.volume.flows.manager import manage_existing_snapshot as manager


class ManageSnapshotFlowTestCase(test.TestCase):
    def setUp(self):
        super(ManageSnapshotFlowTestCase, self).setUp()
        self.ctxt = context.get_admin_context()

    @mock.patch('cinder.objects.snapshot.Snapshot.get_by_id')
    def test_manage_snapshot_after_volume_extending(self, _get_by_id):
        """Test checks snapshot's volume_size value after it is managed."""
        fake_size = 3
        fake_snap = fake_snapshot.fake_snapshot_obj(self.ctxt,
                                                    volume_size=fake_size)
        fake_snap.save = mock.MagicMock()
        _get_by_id.return_value = fake_snap

        real_size = 1
        mock_db = mock.MagicMock()
        mock_driver = mock.MagicMock()
        mock_manage_existing_ref = mock.MagicMock()
        mock_driver.manage_existing_snapshot.return_value = {}

        task = manager.ManageExistingTask(mock_db, mock_driver)
        result = task.execute(self.ctxt, fake_snap, mock_manage_existing_ref,
                              real_size)
        snap_after_manage = result['snapshot']
        #  assure value is equal that size, that we want
        self.assertEqual(real_size, snap_after_manage['volume_size'])
