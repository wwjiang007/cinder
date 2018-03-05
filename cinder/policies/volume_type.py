# Copyright (c) 2017 Huawei Technologies Co., Ltd.
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

from oslo_policy import policy

from cinder.policies import base


MANAGE_POLICY = "volume_extension:types_manage"
ENCRYPTION_POLICY = "volume_extension:volume_type_encryption"
QOS_POLICY = "volume_extension:access_types_qos_specs_id"
EXTRA_SPEC_POLICY = "volume_extension:access_types_extra_specs"

volume_type_policies = [
    policy.DocumentedRuleDefault(
        name=MANAGE_POLICY,
        check_str=base.RULE_ADMIN_API,
        description="Create, update and delete volume type.",
        operations=[
            {
                'method': 'POST',
                'path': '/types'
            },
            {
                'method': 'PUT',
                'path': '/types'
            },
            {
                'method': 'DELETE',
                'path': '/types'
            }
        ]),
    policy.DocumentedRuleDefault(
        name=ENCRYPTION_POLICY,
        check_str=base.RULE_ADMIN_API,
        description="List, show, create, update and delete volume "
                    "type encryption.",
        operations=[
            {
                'method': 'POST',
                'path': '/types/{type_id}/encryption'
            },
            {
                'method': 'PUT',
                'path': '/types/{type_id}/encryption/{encryption_id}'
            },
            {
                'method': 'GET',
                'path': '/types/{type_id}/encryption'
            },
            {
                'method': 'GET',
                'path': '/types/{type_id}/encryption/{encryption_id}'
            },
            {
                'method': 'DELETE',
                'path': '/types/{type_id}/encryption/{encryption_id}'
            }
        ]),
    policy.DocumentedRuleDefault(
        name=EXTRA_SPEC_POLICY,
        check_str=base.RULE_ADMIN_API,
        description="List or show volume type with access type extra "
                    "specs attribute.",
        operations=[
            {
                'method': 'GET',
                'path': '/types/{type_id}'
            },
            {
                'method': 'GET',
                'path': '/types'
            }
        ]),
    policy.DocumentedRuleDefault(
        name=QOS_POLICY,
        check_str=base.RULE_ADMIN_API,
        description="List or show volume type with access type qos specs "
                    "id attribute.",
        operations=[
            {
                'method': 'GET',
                'path': '/types/{type_id}'
            },
            {
                'method': 'GET',
                'path': '/types'
            }
        ]),
]


def list_rules():
    return volume_type_policies
