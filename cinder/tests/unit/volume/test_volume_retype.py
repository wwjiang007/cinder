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
"""Tests for Volume retype Code."""

import mock
from oslo_config import cfg

from cinder import context
from cinder import exception
from cinder import objects
from cinder import quota
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import volume as base
from cinder.volume import volume_types


QUOTAS = quota.QUOTAS

CONF = cfg.CONF


class VolumeRetypeTestCase(base.BaseVolumeTestCase):

    def setUp(self):
        super(VolumeRetypeTestCase, self).setUp()
        self.patch('cinder.volume.utils.clear_volume', autospec=True)
        self.expected_status = 'available'
        self.service_id = 1
        self.user_context = context.RequestContext(user_id=fake.USER_ID,
                                                   project_id=fake.PROJECT_ID)

        volume_types.create(self.context,
                            "old-type",
                            {},
                            description="test-multiattach")
        volume_types.create(self.context,
                            "fake_vol_type",
                            {},
                            description="fake_type")
        volume_types.create(self.context,
                            "multiattach-type",
                            {'multiattach': "<is> True"},
                            description="test-multiattach")
        self.default_vol_type = objects.VolumeType.get_by_name_or_id(
            self.context,
            'fake_vol_type')
        self.multiattach_type = objects.VolumeType.get_by_name_or_id(
            self.context,
            'multiattach-type')

    def fake_get_vtype(self, context, identifier):
        if identifier == "multiattach-type":
            return self.multiattach_type
        else:
            return self.default_vol_type

    @mock.patch.object(volume_types, 'get_by_name_or_id')
    def test_retype_multiattach(self, _mock_get_types):
        """Verify multiattach retype restrictions."""

        _mock_get_types.side_effect = self.fake_get_vtype

        # Test going from default type to multiattach
        vol = self.volume_api.create(self.context,
                                     1,
                                     'test-vol',
                                     '')

        vol.update({'status': 'available'})
        vol.save()
        self.volume_api.retype(self.user_context,
                               vol,
                               'multiattach-type')
        vol = objects.Volume.get_by_id(self.context, vol.id)
        self.assertTrue(vol.multiattach)

        # Test going from multiattach to a non-multiattach type
        vol = self.volume_api.create(
            self.context,
            1,
            'test-multiattachvol',
            '',
            volume_type=self.multiattach_type)
        vol.update({'status': 'available'})
        vol.save()
        self.volume_api.retype(self.user_context,
                               vol,
                               'fake_vol-type')
        vol = objects.Volume.get_by_id(self.context, vol.id)
        self.assertFalse(vol.multiattach)

        # Test trying to retype an in-use volume
        vol.update({'status': 'in-use'})
        vol.save()
        self.assertRaises(exception.InvalidInput,
                          self.volume_api.retype,
                          self.context,
                          vol,
                          'multiattach-type')
