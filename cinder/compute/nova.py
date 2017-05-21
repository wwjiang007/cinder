# Copyright 2013 IBM Corp.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""
Handles all requests to Nova.
"""

from keystoneauth1 import identity
from keystoneauth1 import loading as ks_loading
from novaclient import api_versions
from novaclient import client as nova_client
from novaclient import exceptions as nova_exceptions
from oslo_config import cfg
from oslo_log import log as logging
from requests import exceptions as request_exceptions

from cinder.db import base
from cinder import exception

old_opts = [
    cfg.StrOpt('nova_catalog_info',
               default='compute:Compute Service:publicURL',
               help='Match this value when searching for nova in the '
                    'service catalog. Format is: separated values of '
                    'the form: '
                    '<service_type>:<service_name>:<endpoint_type>',
               deprecated_for_removal=True),
    cfg.StrOpt('nova_catalog_admin_info',
               default='compute:Compute Service:publicURL',
               help='Same as nova_catalog_info, but for admin endpoint.',
               deprecated_for_removal=True),
    cfg.StrOpt('nova_endpoint_template',
               help='Override service catalog lookup with template for nova '
                    'endpoint e.g. http://localhost:8774/v2/%(project_id)s',
               deprecated_for_removal=True),
    cfg.StrOpt('nova_endpoint_admin_template',
               help='Same as nova_endpoint_template, but for admin endpoint.',
               deprecated_for_removal=True),
]

nova_opts = [
    cfg.StrOpt('region_name',
               help='Name of nova region to use. Useful if keystone manages '
                    'more than one region.',
               deprecated_name="os_region_name",
               deprecated_group="DEFAULT"),
    cfg.StrOpt('interface',
               default='public',
               choices=['public', 'admin', 'internal'],
               help='Type of the nova endpoint to use.  This endpoint will '
                    'be looked up in the keystone catalog and should be '
                    'one of public, internal or admin.'),
    cfg.StrOpt('token_auth_url',
               help='The authentication URL for the nova connection when '
                    'using the current user''s token'),
]


NOVA_GROUP = 'nova'
CONF = cfg.CONF

deprecations = {'cafile': [cfg.DeprecatedOpt('nova_ca_certificates_file')],
                'insecure': [cfg.DeprecatedOpt('nova_api_insecure')]}
nova_session_opts = ks_loading.get_session_conf_options(
    deprecated_opts=deprecations)
nova_auth_opts = ks_loading.get_auth_common_conf_options()

CONF.register_opts(old_opts)
CONF.register_opts(nova_opts, group=NOVA_GROUP)
CONF.register_opts(nova_session_opts, group=NOVA_GROUP)
CONF.register_opts(nova_auth_opts, group=NOVA_GROUP)

LOG = logging.getLogger(__name__)

NOVA_API_VERSION = "2.1"

nova_extensions = [ext for ext in
                   nova_client.discover_extensions(NOVA_API_VERSION)
                   if ext.name in ("assisted_volume_snapshots",
                                   "list_extensions")]


def novaclient(context, privileged_user=False, timeout=None):
    """Returns a Nova client

    @param privileged_user: If True, use the account from configuration
        (requires 'auth_type' and the other usual Keystone authentication
        options to be set in the [nova] section)
    @param timeout: Number of seconds to wait for an answer before raising a
        Timeout exception (None to disable)
    """

    if privileged_user and CONF[NOVA_GROUP].auth_type:
        n_auth = ks_loading.load_auth_from_conf_options(CONF, NOVA_GROUP)
    else:
        if CONF[NOVA_GROUP].token_auth_url:
            url = CONF[NOVA_GROUP].token_auth_url
        else:
            # Search for the identity endpoint in the service catalog
            # if nova.token_auth_url is not configured
            matching_endpoints = []
            for service in context.service_catalog:
                if service.get('type') != 'identity':
                    continue
                for endpoint in service['endpoints']:
                    if (not CONF[NOVA_GROUP].region_name or
                       endpoint.get('region') ==
                       CONF[NOVA_GROUP].region_name):
                            matching_endpoints.append(endpoint)
            if not matching_endpoints:
                raise nova_exceptions.EndpointNotFound()
            url = matching_endpoints[0].get(CONF[NOVA_GROUP].interface + 'URL')
        n_auth = identity.Token(auth_url=url,
                                token=context.auth_token,
                                project_name=context.project_name,
                                project_domain_id=context.project_domain)
    keystone_session = ks_loading.load_session_from_conf_options(
        CONF,
        NOVA_GROUP,
        auth=n_auth)

    c = nova_client.Client(api_versions.APIVersion(NOVA_API_VERSION),
                           session=keystone_session,
                           insecure=CONF[NOVA_GROUP].insecure,
                           timeout=timeout,
                           region_name=CONF[NOVA_GROUP].region_name,
                           endpoint_type=CONF[NOVA_GROUP].interface,
                           cacert=CONF[NOVA_GROUP].cafile,
                           extensions=nova_extensions)

    return c


class API(base.Base):
    """API for interacting with novaclient."""

    def has_extension(self, context, extension, timeout=None):
        try:
            nova_exts = novaclient(context).list_extensions.show_all()
        except request_exceptions.Timeout:
            raise exception.APITimeout(service='Nova')
        return extension in [e.name for e in nova_exts]

    def update_server_volume(self, context, server_id, attachment_id,
                             new_volume_id):
        nova = novaclient(context, privileged_user=True)
        nova.volumes.update_server_volume(server_id,
                                          attachment_id,
                                          new_volume_id)

    def create_volume_snapshot(self, context, volume_id, create_info):
        nova = novaclient(context, privileged_user=True)

        # pylint: disable=E1101
        nova.assisted_volume_snapshots.create(
            volume_id,
            create_info=create_info)

    def delete_volume_snapshot(self, context, snapshot_id, delete_info):
        nova = novaclient(context, privileged_user=True)

        # pylint: disable=E1101
        nova.assisted_volume_snapshots.delete(
            snapshot_id,
            delete_info=delete_info)

    def get_server(self, context, server_id, privileged_user=False,
                   timeout=None):
        try:
            return novaclient(context, privileged_user=privileged_user,
                              timeout=timeout).servers.get(server_id)
        except nova_exceptions.NotFound:
            raise exception.ServerNotFound(uuid=server_id)
        except request_exceptions.Timeout:
            raise exception.APITimeout(service='Nova')
