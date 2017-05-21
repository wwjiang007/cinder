# Copyright (c) 2013 - 2015 EMC Corporation.
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
"""
Driver for Dell EMC ScaleIO based on ScaleIO remote CLI.
"""

import base64
import binascii
from distutils import version
import json
import math
from os_brick.initiator import connector
from oslo_config import cfg
from oslo_log import log as logging
from oslo_log import versionutils
from oslo_utils import units
import re
import requests
import six
from six.moves import urllib

from cinder import context
from cinder import exception
from cinder.i18n import _
from cinder.image import image_utils
from cinder import interface
from cinder import utils

from cinder.objects import fields
from cinder.volume import driver
from cinder.volume.drivers.san import san
from cinder.volume import qos_specs
from cinder.volume import utils as volume_utils
from cinder.volume import volume_types

CONF = cfg.CONF

LOG = logging.getLogger(__name__)

scaleio_opts = [
    cfg.StrOpt('sio_rest_server_port',
               default='443',
               help='REST server port.'),
    cfg.BoolOpt('sio_verify_server_certificate',
                default=False,
                help='Verify server certificate.'),
    cfg.StrOpt('sio_server_certificate_path',
               help='Server certificate path.'),
    cfg.BoolOpt('sio_round_volume_capacity',
                default=True,
                help='Round up volume capacity.'),
    cfg.BoolOpt('sio_unmap_volume_before_deletion',
                default=False,
                help='Unmap volume before deletion.'),
    cfg.StrOpt('sio_protection_domain_id',
               help='Protection Domain ID.'),
    cfg.StrOpt('sio_protection_domain_name',
               help='Protection Domain name.'),
    cfg.StrOpt('sio_storage_pools',
               help='Storage Pools.'),
    cfg.StrOpt('sio_storage_pool_name',
               help='Storage Pool name.'),
    cfg.StrOpt('sio_storage_pool_id',
               help='Storage Pool ID.'),
    cfg.StrOpt('sio_server_api_version',
               help='ScaleIO API version.'),
    cfg.FloatOpt('sio_max_over_subscription_ratio',
                 # This option exists to provide a default value for the
                 # ScaleIO driver which is different than the global default.
                 default=10.0,
                 help='max_over_subscription_ratio setting for the ScaleIO '
                      'driver. This replaces the general '
                      'max_over_subscription_ratio which has no effect '
                      'in this driver.'
                      'Maximum value allowed for ScaleIO is 10.0.')
]

CONF.register_opts(scaleio_opts)

STORAGE_POOL_NAME = 'sio:sp_name'
STORAGE_POOL_ID = 'sio:sp_id'
PROTECTION_DOMAIN_NAME = 'sio:pd_name'
PROTECTION_DOMAIN_ID = 'sio:pd_id'
PROVISIONING_KEY = 'provisioning:type'
OLD_PROVISIONING_KEY = 'sio:provisioning_type'
IOPS_LIMIT_KEY = 'sio:iops_limit'
BANDWIDTH_LIMIT = 'sio:bandwidth_limit'
QOS_IOPS_LIMIT_KEY = 'maxIOPS'
QOS_BANDWIDTH_LIMIT = 'maxBWS'
QOS_IOPS_PER_GB = 'maxIOPSperGB'
QOS_BANDWIDTH_PER_GB = 'maxBWSperGB'

BLOCK_SIZE = 8
OK_STATUS_CODE = 200
VOLUME_NOT_FOUND_ERROR = 79
# This code belongs to older versions of ScaleIO
OLD_VOLUME_NOT_FOUND_ERROR = 78
VOLUME_NOT_MAPPED_ERROR = 84
ILLEGAL_SYNTAX = 0
VOLUME_ALREADY_MAPPED_ERROR = 81
MIN_BWS_SCALING_SIZE = 128
SIO_MAX_OVERSUBSCRIPTION_RATIO = 10.0


@interface.volumedriver
class ScaleIODriver(driver.VolumeDriver):
    """Dell EMC ScaleIO Driver."""

    VERSION = "2.0.2"
    # Major changes
    # 2.0.1: Added support for SIO 1.3x in addition to 2.0.x
    # 2.0.2: Added consistency group support to generic volume groups

    # ThirdPartySystems wiki
    CI_WIKI_NAME = "EMC_ScaleIO_CI"

    scaleio_qos_keys = (QOS_IOPS_LIMIT_KEY, QOS_BANDWIDTH_LIMIT,
                        QOS_IOPS_PER_GB, QOS_BANDWIDTH_PER_GB)

    def __init__(self, *args, **kwargs):
        super(ScaleIODriver, self).__init__(*args, **kwargs)

        self.configuration.append_config_values(san.san_opts)
        self.configuration.append_config_values(scaleio_opts)
        self.server_ip = self.configuration.san_ip
        self.server_port = self.configuration.sio_rest_server_port
        self.server_username = self.configuration.san_login
        self.server_password = self.configuration.san_password
        self.server_token = None
        self.server_api_version = self.configuration.sio_server_api_version
        self.verify_server_certificate = (
            self.configuration.sio_verify_server_certificate)
        self.server_certificate_path = None
        if self.verify_server_certificate:
            self.server_certificate_path = (
                self.configuration.sio_server_certificate_path)
        LOG.info("REST server IP: %(ip)s, port: %(port)s, username: %("
                 "user)s. Verify server's certificate: %(verify_cert)s.",
                 {'ip': self.server_ip,
                  'port': self.server_port,
                  'user': self.server_username,
                  'verify_cert': self.verify_server_certificate})

        self.storage_pools = None
        if self.configuration.sio_storage_pools:
            self.storage_pools = [
                e.strip() for e in
                self.configuration.sio_storage_pools.split(',')]

        self.storage_pool_name = self.configuration.sio_storage_pool_name
        self.storage_pool_id = self.configuration.sio_storage_pool_id
        if self.storage_pool_name is None and self.storage_pool_id is None:
            LOG.warning("No storage pool name or id was found.")
        else:
            LOG.info("Storage pools names: %(pools)s, "
                     "storage pool name: %(pool)s, pool id: %(pool_id)s.",
                     {'pools': self.storage_pools,
                      'pool': self.storage_pool_name,
                      'pool_id': self.storage_pool_id})

        self.protection_domain_name = (
            self.configuration.sio_protection_domain_name)
        LOG.info("Protection domain name: %(domain_name)s.",
                 {'domain_name': self.protection_domain_name})
        self.protection_domain_id = self.configuration.sio_protection_domain_id
        LOG.info("Protection domain id: %(domain_id)s.",
                 {'domain_id': self.protection_domain_id})

        self.provisioning_type = (
            'thin' if self.configuration.san_thin_provision else 'thick')
        LOG.info("Default provisioning type: %(provisioning_type)s.",
                 {'provisioning_type': self.provisioning_type})
        self.configuration.max_over_subscription_ratio = (
            self.configuration.sio_max_over_subscription_ratio)
        self.connector = connector.InitiatorConnector.factory(
            connector.SCALEIO, utils.get_root_helper(),
            self.configuration.num_volume_device_scan_tries
        )

        self.connection_properties = {
            'scaleIO_volname': None,
            'hostIP': None,
            'serverIP': self.server_ip,
            'serverPort': self.server_port,
            'serverUsername': self.server_username,
            'serverPassword': self.server_password,
            'serverToken': self.server_token,
            'iopsLimit': None,
            'bandwidthLimit': None,
        }

    def check_for_setup_error(self):
        if (not self.protection_domain_name and
                not self.protection_domain_id):
            LOG.warning("No protection domain name or id "
                        "was specified in configuration.")

        if self.protection_domain_name and self.protection_domain_id:
            msg = _("Cannot specify both protection domain name "
                    "and protection domain id.")
            raise exception.InvalidInput(reason=msg)

        if not self.server_ip:
            msg = _("REST server IP must by specified.")
            raise exception.InvalidInput(reason=msg)

        if not self.server_username:
            msg = _("REST server username must by specified.")
            raise exception.InvalidInput(reason=msg)

        if not self.server_password:
            msg = _("REST server password must by specified.")
            raise exception.InvalidInput(reason=msg)

        if not self.verify_server_certificate:
            LOG.warning("Verify certificate is not set, using default of "
                        "False.")

        if self.verify_server_certificate and not self.server_certificate_path:
            msg = _("Path to REST server's certificate must be specified.")
            raise exception.InvalidInput(reason=msg)

        if self.storage_pool_name and self.storage_pool_id:
            msg = _("Cannot specify both storage pool name and storage "
                    "pool id.")
            raise exception.InvalidInput(reason=msg)

        if not self.storage_pool_name and not self.storage_pool_id:
            msg = _("Must specify storage pool name or id.")
            raise exception.InvalidInput(reason=msg)

        if not self.storage_pools:
            msg = (_("Must specify storage pools. Option: "
                     "sio_storage_pools."))
            raise exception.InvalidInput(reason=msg)

        if (self.configuration.max_over_subscription_ratio is not None and
            (self.configuration.max_over_subscription_ratio -
             SIO_MAX_OVERSUBSCRIPTION_RATIO > 1)):
            msg = (_("Max over subscription is configured to %(ratio)1f "
                     "while ScaleIO support up to %(sio_ratio)s.") %
                   {'sio_ratio': SIO_MAX_OVERSUBSCRIPTION_RATIO,
                    'ratio': self.configuration.max_over_subscription_ratio})
            raise exception.InvalidInput(reason=msg)

        server_api_version = self._get_server_api_version(fromcache=False)
        if not self._version_greater_than_or_equal(
                server_api_version, "2.0.0"):
            # we are running against a pre-2.0.0 ScaleIO instance
            msg = (_("Using ScaleIO versions less than v2.0.0 has been "
                     "deprecated and will be removed in a future version"))
            versionutils.report_deprecated_feature(LOG, msg)

    def _find_storage_pool_id_from_storage_type(self, storage_type):
        # Default to what was configured in configuration file if not defined.
        return storage_type.get(STORAGE_POOL_ID,
                                self.storage_pool_id)

    def _find_storage_pool_name_from_storage_type(self, storage_type):
        return storage_type.get(STORAGE_POOL_NAME,
                                self.storage_pool_name)

    def _find_protection_domain_id_from_storage_type(self, storage_type):
        # Default to what was configured in configuration file if not defined.
        return storage_type.get(PROTECTION_DOMAIN_ID,
                                self.protection_domain_id)

    def _find_protection_domain_name_from_storage_type(self, storage_type):
        # Default to what was configured in configuration file if not defined.
        return storage_type.get(PROTECTION_DOMAIN_NAME,
                                self.protection_domain_name)

    def _find_provisioning_type(self, storage_type):
        new_provisioning_type = storage_type.get(PROVISIONING_KEY)
        old_provisioning_type = storage_type.get(OLD_PROVISIONING_KEY)
        if new_provisioning_type is None and old_provisioning_type is not None:
            LOG.info("Using sio:provisioning_type for defining "
                     "thin or thick volume will be deprecated in the "
                     "Ocata release of OpenStack. Please use "
                     "provisioning:type configuration option.")
            provisioning_type = old_provisioning_type
        else:
            provisioning_type = new_provisioning_type

        if provisioning_type is not None:
            if provisioning_type not in ('thick', 'thin'):
                msg = _("Illegal provisioning type. The supported "
                        "provisioning types are 'thick' or 'thin'.")
                raise exception.VolumeBackendAPIException(data=msg)
            return provisioning_type
        else:
            return self.provisioning_type

    @staticmethod
    def _find_limit(storage_type, qos_key, extraspecs_key):
        qos_limit = (storage_type.get(qos_key)
                     if qos_key is not None else None)
        extraspecs_limit = (storage_type.get(extraspecs_key)
                            if extraspecs_key is not None else None)
        if extraspecs_limit is not None:
            if qos_limit is not None:
                LOG.warning("QoS specs are overriding extra_specs.")
            else:
                LOG.info("Using extra_specs for defining QoS specs "
                         "will be deprecated in the N release "
                         "of OpenStack. Please use QoS specs.")
        return qos_limit if qos_limit is not None else extraspecs_limit

    @staticmethod
    def _version_greater_than(ver1, ver2):
        return version.LooseVersion(ver1) > version.LooseVersion(ver2)

    @staticmethod
    def _version_greater_than_or_equal(ver1, ver2):
        return version.LooseVersion(ver1) >= version.LooseVersion(ver2)

    @staticmethod
    def _id_to_base64(id):
        # Base64 encode the id to get a volume name less than 32 characters due
        # to ScaleIO limitation.
        name = six.text_type(id).replace("-", "")
        try:
            name = base64.b16decode(name.upper())
        except (TypeError, binascii.Error):
            pass
        encoded_name = name
        if isinstance(encoded_name, six.text_type):
            encoded_name = encoded_name.encode('utf-8')
        encoded_name = base64.b64encode(encoded_name)
        if six.PY3:
            encoded_name = encoded_name.decode('ascii')
        LOG.debug("Converted id %(id)s to scaleio name %(name)s.",
                  {'id': id, 'name': encoded_name})
        return encoded_name

    def create_volume(self, volume):
        """Creates a scaleIO volume."""
        self._check_volume_size(volume.size)

        volname = self._id_to_base64(volume.id)

        storage_type = self._get_volumetype_extraspecs(volume)
        storage_pool_name = self._find_storage_pool_name_from_storage_type(
            storage_type)
        storage_pool_id = self._find_storage_pool_id_from_storage_type(
            storage_type)
        protection_domain_id = (
            self._find_protection_domain_id_from_storage_type(storage_type))
        protection_domain_name = (
            self._find_protection_domain_name_from_storage_type(storage_type))
        provisioning_type = self._find_provisioning_type(storage_type)

        LOG.info("Volume type: %(volume_type)s, "
                 "storage pool name: %(pool_name)s, "
                 "storage pool id: %(pool_id)s, protection domain id: "
                 "%(domain_id)s, protection domain name: %(domain_name)s.",
                 {'volume_type': storage_type,
                  'pool_name': storage_pool_name,
                  'pool_id': storage_pool_id,
                  'domain_id': protection_domain_id,
                  'domain_name': protection_domain_name})

        if storage_pool_name:
            self.storage_pool_name = storage_pool_name
            self.storage_pool_id = None
        if storage_pool_id:
            self.storage_pool_id = storage_pool_id
            self.storage_pool_name = None
        if protection_domain_name:
            self.protection_domain_name = protection_domain_name
            self.protection_domain_id = None
        if protection_domain_id:
            self.protection_domain_id = protection_domain_id
            self.protection_domain_name = None

        domain_id = self.protection_domain_id
        if not domain_id:
            if not self.protection_domain_name:
                msg = _("Must specify protection domain name or"
                        " protection domain id.")
                raise exception.VolumeBackendAPIException(data=msg)

            domain_name = self.protection_domain_name
            encoded_domain_name = urllib.parse.quote(domain_name, '')
            req_vars = {'server_ip': self.server_ip,
                        'server_port': self.server_port,
                        'encoded_domain_name': encoded_domain_name}
            request = ("https://%(server_ip)s:%(server_port)s"
                       "/api/types/Domain/instances/getByName::"
                       "%(encoded_domain_name)s") % req_vars
            LOG.info("ScaleIO get domain id by name request: %s.",
                     request)

            r, domain_id = self._execute_scaleio_get_request(request)

            if not domain_id:
                msg = (_("Domain with name %s wasn't found.")
                       % self.protection_domain_name)
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
            if r.status_code != OK_STATUS_CODE and "errorCode" in domain_id:
                msg = (_("Error getting domain id from name %(name)s: %(id)s.")
                       % {'name': self.protection_domain_name,
                          'id': domain_id['message']})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        LOG.info("Domain id is %s.", domain_id)
        pool_name = self.storage_pool_name
        pool_id = self.storage_pool_id
        if pool_name:
            encoded_domain_name = urllib.parse.quote(pool_name, '')
            req_vars = {'server_ip': self.server_ip,
                        'server_port': self.server_port,
                        'domain_id': domain_id,
                        'encoded_domain_name': encoded_domain_name}
            request = ("https://%(server_ip)s:%(server_port)s"
                       "/api/types/Pool/instances/getByName::"
                       "%(domain_id)s,%(encoded_domain_name)s") % req_vars
            LOG.info("ScaleIO get pool id by name request: %s.", request)
            r, pool_id = self._execute_scaleio_get_request(request)

            if not pool_id:
                msg = (_("Pool with name %(pool_name)s wasn't found in "
                         "domain %(domain_id)s.")
                       % {'pool_name': pool_name,
                          'domain_id': domain_id})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
            if r.status_code != OK_STATUS_CODE and "errorCode" in pool_id:
                msg = (_("Error getting pool id from name %(pool_name)s: "
                         "%(err_msg)s.")
                       % {'pool_name': pool_name,
                          'err_msg': pool_id['message']})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        LOG.info("Pool id is %s.", pool_id)
        if provisioning_type == 'thin':
            provisioning = "ThinProvisioned"
        # Default volume type is thick.
        else:
            provisioning = "ThickProvisioned"

        # units.Mi = 1024 ** 2
        volume_size_kb = volume.size * units.Mi
        params = {'protectionDomainId': domain_id,
                  'volumeSizeInKb': six.text_type(volume_size_kb),
                  'name': volname,
                  'volumeType': provisioning,
                  'storagePoolId': pool_id}

        LOG.info("Params for add volume request: %s.", params)
        req_vars = {'server_ip': self.server_ip,
                    'server_port': self.server_port}
        request = ("https://%(server_ip)s:%(server_port)s"
                   "/api/types/Volume/instances") % req_vars
        r, response = self._execute_scaleio_post_request(params, request)

        LOG.info("Add volume response: %s", response)

        if r.status_code != OK_STATUS_CODE and "errorCode" in response:
            msg = (_("Error creating volume: %s.") % response['message'])
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        LOG.info("Created volume %(volname)s, volume id %(volid)s.",
                 {'volname': volname, 'volid': volume.id})

        real_size = int(self._round_to_num_gran(volume.size))

        return {'provider_id': response['id'], 'size': real_size}

    def _check_volume_size(self, size):
        if size % 8 != 0:
            round_volume_capacity = (
                self.configuration.sio_round_volume_capacity)
            if not round_volume_capacity:
                exception_msg = (_(
                                 "Cannot create volume of size %s: "
                                 "not multiple of 8GB.") % size)
                LOG.error(exception_msg)
                raise exception.VolumeBackendAPIException(data=exception_msg)

    def create_snapshot(self, snapshot):
        """Creates a scaleio snapshot."""
        volume_id = snapshot.volume.provider_id
        snapname = self._id_to_base64(snapshot.id)
        return self._snapshot_volume(volume_id, snapname)

    def _snapshot_volume(self, vol_id, snapname):
        LOG.info("Snapshot volume %(vol)s into snapshot %(id)s.",
                 {'vol': vol_id, 'id': snapname})
        params = {
            'snapshotDefs': [{"volumeId": vol_id, "snapshotName": snapname}]}
        req_vars = {'server_ip': self.server_ip,
                    'server_port': self.server_port}
        request = ("https://%(server_ip)s:%(server_port)s"
                   "/api/instances/System/action/snapshotVolumes") % req_vars
        r, response = self._execute_scaleio_post_request(params, request)
        LOG.info("Snapshot volume response: %s.", response)
        if r.status_code != OK_STATUS_CODE and "errorCode" in response:
            msg = (_("Failed creating snapshot for volume %(volname)s: "
                     "%(response)s.") %
                   {'volname': vol_id,
                    'response': response['message']})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        return {'provider_id': response['volumeIdList'][0]}

    def _execute_scaleio_post_request(self, params, request):
        r = requests.post(
            request,
            data=json.dumps(params),
            headers=self._get_headers(),
            auth=(
                self.server_username,
                self.server_token),
            verify=self._get_verify_cert())
        r = self._check_response(r, request, False, params)
        response = None
        try:
            response = r.json()
        except ValueError:
            response = None
        return r, response

    def _check_response(self, response, request, is_get_request=True,
                        params=None):
        if response.status_code == 401 or response.status_code == 403:
            LOG.info("Token is invalid, going to re-login and get "
                     "a new one.")
            login_request = (
                "https://" + self.server_ip +
                ":" + self.server_port + "/api/login")
            verify_cert = self._get_verify_cert()
            r = requests.get(
                login_request,
                auth=(
                    self.server_username,
                    self.server_password),
                verify=verify_cert)
            token = r.json()
            self.server_token = token
            # Repeat request with valid token.
            LOG.info("Going to perform request again %s with valid token.",
                     request)
            if is_get_request:
                res = requests.get(request,
                                   auth=(self.server_username,
                                         self.server_token),
                                   verify=verify_cert)
            else:
                res = requests.post(request,
                                    data=json.dumps(params),
                                    headers=self._get_headers(),
                                    auth=(self.server_username,
                                          self.server_token),
                                    verify=verify_cert)
            return res
        return response

    def _get_server_api_version(self, fromcache=True):
        if self.server_api_version is None or fromcache is False:
            request = (
                "https://" + self.server_ip +
                ":" + self.server_port + "/api/version")
            r, unused = self._execute_scaleio_get_request(request)

            if r.status_code == OK_STATUS_CODE:
                self.server_api_version = r.text.replace('\"', '')
                LOG.info("REST API Version: %(api_version)s",
                         {'api_version': self.server_api_version})
            else:
                msg = (_("Error calling version api "
                         "status code: %d") % r.status_code)
                raise exception.VolumeBackendAPIException(data=msg)

            # make sure the response was valid
            pattern = re.compile("^\d+(\.\d+)*$")
            if not pattern.match(self.server_api_version):
                msg = (_("Error calling version api "
                         "response: %s") % r.text)
                raise exception.VolumeBackendAPIException(data=msg)

        return self.server_api_version

    def create_volume_from_snapshot(self, volume, snapshot):
        """Creates a volume from a snapshot."""
        # We interchange 'volume' and 'snapshot' because in ScaleIO
        # snapshot is a volume: once a snapshot is generated it
        # becomes a new unmapped volume in the system and the user
        # may manipulate it in the same manner as any other volume
        # exposed by the system
        volume_id = snapshot.provider_id
        snapname = self._id_to_base64(volume.id)
        LOG.info("ScaleIO create volume from snapshot: snapshot %(snapname)s "
                 "to volume %(volname)s.",
                 {'volname': volume_id,
                  'snapname': snapname})

        return self._snapshot_volume(volume_id, snapname)

    @staticmethod
    def _get_headers():
        return {'content-type': 'application/json'}

    def _get_verify_cert(self):
        verify_cert = False
        if self.verify_server_certificate:
            verify_cert = self.server_certificate_path
        return verify_cert

    def extend_volume(self, volume, new_size):
        """Extends the size of an existing available ScaleIO volume.

        This action will round up the volume to the nearest size that is
        a granularity of 8 GBs.
        """
        return self._extend_volume(volume['provider_id'], volume.size,
                                   new_size)

    def _extend_volume(self, volume_id, old_size, new_size):
        vol_id = volume_id
        LOG.info(
            "ScaleIO extend volume: volume %(volname)s to size %(new_size)s.",
            {'volname': vol_id,
             'new_size': new_size})

        req_vars = {'server_ip': self.server_ip,
                    'server_port': self.server_port,
                    'vol_id': vol_id}
        request = ("https://%(server_ip)s:%(server_port)s"
                   "/api/instances/Volume::%(vol_id)s"
                   "/action/setVolumeSize") % req_vars
        LOG.info("Change volume capacity request: %s.", request)

        # Round up the volume size so that it is a granularity of 8 GBs
        # because ScaleIO only supports volumes with a granularity of 8 GBs.
        volume_new_size = self._round_to_num_gran(new_size)
        volume_real_old_size = self._round_to_num_gran(old_size)
        if volume_real_old_size == volume_new_size:
            return

        round_volume_capacity = self.configuration.sio_round_volume_capacity
        if not round_volume_capacity and not new_size % 8 == 0:
            LOG.warning("ScaleIO only supports volumes with a granularity "
                        "of 8 GBs. The new volume size is: %d.",
                        volume_new_size)

        params = {'sizeInGB': six.text_type(volume_new_size)}
        r, response = self._execute_scaleio_post_request(params, request)
        if r.status_code != OK_STATUS_CODE:
            response = r.json()
            msg = (_("Error extending volume %(vol)s: %(err)s.")
                   % {'vol': vol_id,
                      'err': response['message']})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

    @staticmethod
    def _round_to_num_gran(size, num=8):
        if size % num == 0:
            return size
        return size + num - (size % num)

    @staticmethod
    def _round_down_to_num_gran(size, num=8):
        return size - (size % num)

    def create_cloned_volume(self, volume, src_vref):
        """Creates a cloned volume."""
        volume_id = src_vref['provider_id']
        snapname = self._id_to_base64(volume.id)
        LOG.info("ScaleIO create cloned volume: source volume %(src)s to "
                 "target volume %(tgt)s.",
                 {'src': volume_id,
                  'tgt': snapname})

        ret = self._snapshot_volume(volume_id, snapname)
        if volume.size > src_vref.size:
            self._extend_volume(ret['provider_id'], src_vref.size, volume.size)

        return ret

    def delete_volume(self, volume):
        """Deletes a self.logical volume"""
        volume_id = volume['provider_id']
        self._delete_volume(volume_id)

    def _delete_volume(self, vol_id):
        req_vars = {'server_ip': self.server_ip,
                    'server_port': self.server_port,
                    'vol_id': six.text_type(vol_id)}

        unmap_before_delete = (
            self.configuration.sio_unmap_volume_before_deletion)
        # Ensure that the volume is not mapped to any SDC before deletion in
        # case unmap_before_deletion is enabled.
        if unmap_before_delete:
            params = {'allSdcs': ''}
            request = ("https://%(server_ip)s:%(server_port)s"
                       "/api/instances/Volume::%(vol_id)s"
                       "/action/removeMappedSdc") % req_vars
            LOG.info("Trying to unmap volume from all sdcs"
                     " before deletion: %s.",
                     request)
            r, unused = self._execute_scaleio_post_request(params, request)
            LOG.debug("Unmap volume response: %s.", r.text)

        params = {'removeMode': 'ONLY_ME'}
        request = ("https://%(server_ip)s:%(server_port)s"
                   "/api/instances/Volume::%(vol_id)s"
                   "/action/removeVolume") % req_vars
        r, response = self._execute_scaleio_post_request(params, request)

        if r.status_code != OK_STATUS_CODE:
            error_code = response['errorCode']
            if error_code == VOLUME_NOT_FOUND_ERROR:
                LOG.warning("Ignoring error in delete volume %s:"
                            " Volume not found.", vol_id)
            elif vol_id is None:
                LOG.warning("Volume does not have provider_id thus does not "
                            "map to a ScaleIO volume. "
                            "Allowing deletion to proceed.")
            else:
                msg = (_("Error deleting volume %(vol)s: %(err)s.") %
                       {'vol': vol_id,
                        'err': response['message']})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

    def delete_snapshot(self, snapshot):
        """Deletes a ScaleIO snapshot."""
        snap_id = snapshot.provider_id
        LOG.info("ScaleIO delete snapshot.")
        return self._delete_volume(snap_id)

    def initialize_connection(self, volume, connector, **kwargs):
        """Initializes the connection and returns connection info.

        The scaleio driver returns a driver_volume_type of 'scaleio'.
        """

        LOG.debug("Connector is %s.", connector)
        connection_properties = dict(self.connection_properties)

        volname = self._id_to_base64(volume.id)
        connection_properties['scaleIO_volname'] = volname
        connection_properties['scaleIO_volume_id'] = volume.provider_id
        extra_specs = self._get_volumetype_extraspecs(volume)
        qos_specs = self._get_volumetype_qos(volume)
        storage_type = extra_specs.copy()
        storage_type.update(qos_specs)
        LOG.info("Volume type is %s.", storage_type)
        round_volume_size = self._round_to_num_gran(volume.size)
        iops_limit = self._get_iops_limit(round_volume_size, storage_type)
        bandwidth_limit = self._get_bandwidth_limit(round_volume_size,
                                                    storage_type)
        LOG.info("iops limit is %s", iops_limit)
        LOG.info("bandwidth limit is %s", bandwidth_limit)
        connection_properties['iopsLimit'] = iops_limit
        connection_properties['bandwidthLimit'] = bandwidth_limit
        return {'driver_volume_type': 'scaleio',
                'data': connection_properties}

    def _get_bandwidth_limit(self, size, storage_type):
        try:
            max_bandwidth = self._find_limit(storage_type, QOS_BANDWIDTH_LIMIT,
                                             BANDWIDTH_LIMIT)
            if max_bandwidth is not None:
                max_bandwidth = (self._round_to_num_gran(int(max_bandwidth),
                                                         units.Ki))
                max_bandwidth = six.text_type(max_bandwidth)
            LOG.info("max bandwidth is: %s", max_bandwidth)
            bw_per_gb = self._find_limit(storage_type, QOS_BANDWIDTH_PER_GB,
                                         None)
            LOG.info("bandwidth per gb is: %s", bw_per_gb)
            if bw_per_gb is None:
                return max_bandwidth
            # Since ScaleIO volumes size is in 8GB granularity
            # and BWS limitation is in 1024 KBs granularity, we need to make
            # sure that scaled_bw_limit is in 128 granularity.
            scaled_bw_limit = (size *
                               self._round_to_num_gran(int(bw_per_gb),
                                                       MIN_BWS_SCALING_SIZE))
            if max_bandwidth is None or scaled_bw_limit < int(max_bandwidth):
                return six.text_type(scaled_bw_limit)
            else:
                return max_bandwidth
        except ValueError:
            msg = _("None numeric BWS QoS limitation")
            raise exception.InvalidInput(reason=msg)

    def _get_iops_limit(self, size, storage_type):
        max_iops = self._find_limit(storage_type, QOS_IOPS_LIMIT_KEY,
                                    IOPS_LIMIT_KEY)
        LOG.info("max iops is: %s", max_iops)
        iops_per_gb = self._find_limit(storage_type, QOS_IOPS_PER_GB, None)
        LOG.info("iops per gb is: %s", iops_per_gb)
        try:
            if iops_per_gb is None:
                if max_iops is not None:
                    return six.text_type(max_iops)
                else:
                    return None
            scaled_iops_limit = size * int(iops_per_gb)
            if max_iops is None or scaled_iops_limit < int(max_iops):
                return six.text_type(scaled_iops_limit)
            else:
                return six.text_type(max_iops)
        except ValueError:
            msg = _("None numeric IOPS QoS limitation")
            raise exception.InvalidInput(reason=msg)

    def terminate_connection(self, volume, connector, **kwargs):
        LOG.debug("scaleio driver terminate connection.")

    def _update_volume_stats(self):
        stats = {}

        backend_name = self.configuration.safe_get('volume_backend_name')
        stats['volume_backend_name'] = backend_name or 'scaleio'
        stats['vendor_name'] = 'Dell EMC'
        stats['driver_version'] = self.VERSION
        stats['storage_protocol'] = 'scaleio'
        stats['reserved_percentage'] = 0
        stats['QoS_support'] = True
        stats['consistent_group_snapshot_enabled'] = True
        stats['thick_provisioning_support'] = True
        stats['thin_provisioning_support'] = True
        pools = []

        verify_cert = self._get_verify_cert()

        free_capacity = 0
        total_capacity = 0
        provisioned_capacity = 0

        for sp_name in self.storage_pools:
            splitted_name = sp_name.split(':')
            domain_name = splitted_name[0]
            pool_name = splitted_name[1]
            LOG.debug("domain name is %(domain)s, pool name is %(pool)s.",
                      {'domain': domain_name,
                       'pool': pool_name})
            # Get domain id from name.
            encoded_domain_name = urllib.parse.quote(domain_name, '')
            req_vars = {'server_ip': self.server_ip,
                        'server_port': self.server_port,
                        'encoded_domain_name': encoded_domain_name}
            request = ("https://%(server_ip)s:%(server_port)s"
                       "/api/types/Domain/instances/getByName::"
                       "%(encoded_domain_name)s") % req_vars
            LOG.info("ScaleIO get domain id by name request: %s.",
                     request)
            LOG.info("username: %(username)s, verify_cert: %(verify)s.",
                     {'username': self.server_username,
                      'verify': verify_cert})
            r, domain_id = self._execute_scaleio_get_request(request)
            if not domain_id:
                msg = (_("Domain with name %s wasn't found.")
                       % self.protection_domain_name)
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
            if r.status_code != OK_STATUS_CODE and "errorCode" in domain_id:
                msg = (_("Error getting domain id from name %(name)s: "
                         "%(err)s.")
                       % {'name': self.protection_domain_name,
                          'err': domain_id['message']})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
            LOG.info("Domain id is %s.", domain_id)

            # Get pool id from name.
            encoded_pool_name = urllib.parse.quote(pool_name, '')
            req_vars = {'server_ip': self.server_ip,
                        'server_port': self.server_port,
                        'domain_id': domain_id,
                        'encoded_pool_name': encoded_pool_name}
            request = ("https://%(server_ip)s:%(server_port)s"
                       "/api/types/Pool/instances/getByName::"
                       "%(domain_id)s,%(encoded_pool_name)s") % req_vars
            LOG.info("ScaleIO get pool id by name request: %s.", request)
            r, pool_id = self._execute_scaleio_get_request(request)
            if not pool_id:
                msg = (_("Pool with name %(pool)s wasn't found in domain "
                         "%(domain)s.")
                       % {'pool': pool_name,
                          'domain': domain_id})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
            if r.status_code != OK_STATUS_CODE and "errorCode" in pool_id:
                msg = (_("Error getting pool id from name %(pool)s: "
                         "%(err)s.")
                       % {'pool': pool_name,
                          'err': pool_id['message']})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
            LOG.info("Pool id is %s.", pool_id)
            req_vars = {'server_ip': self.server_ip,
                        'server_port': self.server_port}
            request = ("https://%(server_ip)s:%(server_port)s"
                       "/api/types/StoragePool/instances/action/"
                       "querySelectedStatistics") % req_vars
            # SIO version 2+ added a property...
            if self._version_greater_than_or_equal(
                    self._get_server_api_version(),
                    "2.0.0"):
                # The 'Km' in thinCapacityAllocatedInKm is a bug in REST API
                params = {'ids': [pool_id], 'properties': [
                    "capacityAvailableForVolumeAllocationInKb",
                    "capacityLimitInKb", "spareCapacityInKb",
                    "thickCapacityInUseInKb", "thinCapacityAllocatedInKm"]}
            else:
                params = {'ids': [pool_id], 'properties': [
                    "capacityAvailableForVolumeAllocationInKb",
                    "capacityLimitInKb", "spareCapacityInKb",
                    "thickCapacityInUseInKb"]}

            r, response = self._execute_scaleio_post_request(params, request)
            LOG.info("Query capacity stats response: %s.", response)
            for res in response.values():
                # Divide by two because ScaleIO creates a copy for each volume
                total_capacity_kb = (
                    (res['capacityLimitInKb'] - res['spareCapacityInKb']) / 2)
                total_capacity_gb = (self._round_down_to_num_gran
                                     (total_capacity_kb / units.Mi))
                # This property is already rounded
                # to 8 GB granularity in backend
                free_capacity_gb = (
                    res['capacityAvailableForVolumeAllocationInKb'] / units.Mi)
                # Divide by two because ScaleIO creates a copy for each volume
                if self._version_greater_than_or_equal(
                        self._get_server_api_version(),
                        "2.0.0"):
                    provisioned_capacity = (
                        ((res['thickCapacityInUseInKb'] +
                          res['thinCapacityAllocatedInKm']) / 2) / units.Mi)
                else:
                    provisioned_capacity = (
                        (res['thickCapacityInUseInKb'] / 2) / units.Mi)
                LOG.info("free capacity of pool %(pool)s is: %(free)s, "
                         "total capacity: %(total)s, "
                         "provisioned capacity: %(prov)s",
                         {'pool': pool_name,
                          'free': free_capacity_gb,
                          'total': total_capacity_gb,
                          'prov': provisioned_capacity})
            pool = {'pool_name': sp_name,
                    'total_capacity_gb': total_capacity_gb,
                    'free_capacity_gb': free_capacity_gb,
                    'QoS_support': True,
                    'consistent_group_snapshot_enabled': True,
                    'reserved_percentage': 0,
                    'thin_provisioning_support': True,
                    'thick_provisioning_support': True,
                    'provisioned_capacity_gb': provisioned_capacity,
                    'max_over_subscription_ratio':
                        self.configuration.max_over_subscription_ratio
                    }

            pools.append(pool)
            free_capacity += free_capacity_gb
            total_capacity += total_capacity_gb

        stats['total_capacity_gb'] = total_capacity
        stats['free_capacity_gb'] = free_capacity
        LOG.info("Free capacity for backend is: %(free)s, total capacity: "
                 "%(total)s.",
                 {'free': free_capacity,
                  'total': total_capacity})

        stats['pools'] = pools

        LOG.info("Backend name is %s.", stats["volume_backend_name"])

        self._stats = stats

    def get_volume_stats(self, refresh=False):
        """Get volume stats.

        If 'refresh' is True, run update the stats first.
        """
        if refresh:
            self._update_volume_stats()

        return self._stats

    @staticmethod
    def _get_volumetype_extraspecs(volume):
        specs = {}
        ctxt = context.get_admin_context()
        type_id = volume['volume_type_id']
        if type_id:
            volume_type = volume_types.get_volume_type(ctxt, type_id)
            specs = volume_type.get('extra_specs')
            for key, value in specs.items():
                specs[key] = value

        return specs

    def _get_volumetype_qos(self, volume):
        qos = {}
        ctxt = context.get_admin_context()
        type_id = volume['volume_type_id']
        if type_id:
            volume_type = volume_types.get_volume_type(ctxt, type_id)
            qos_specs_id = volume_type.get('qos_specs_id')
            if qos_specs_id is not None:
                specs = qos_specs.get_qos_specs(ctxt, qos_specs_id)['specs']
            else:
                specs = {}
            for key, value in specs.items():
                if key in self.scaleio_qos_keys:
                    qos[key] = value
        return qos

    def _sio_attach_volume(self, volume):
        """Call connector.connect_volume() and return the path. """
        LOG.debug("Calling os-brick to attach ScaleIO volume.")
        connection_properties = dict(self.connection_properties)
        connection_properties['scaleIO_volname'] = self._id_to_base64(
            volume.id)
        connection_properties['scaleIO_volume_id'] = volume.provider_id
        device_info = self.connector.connect_volume(connection_properties)
        return device_info['path']

    def _sio_detach_volume(self, volume):
        """Call the connector.disconnect() """
        LOG.info("Calling os-brick to detach ScaleIO volume.")
        connection_properties = dict(self.connection_properties)
        connection_properties['scaleIO_volname'] = self._id_to_base64(
            volume.id)
        connection_properties['scaleIO_volume_id'] = volume.provider_id
        self.connector.disconnect_volume(connection_properties, volume)

    def copy_image_to_volume(self, context, volume, image_service, image_id):
        """Fetch the image from image_service and write it to the volume."""
        LOG.info("ScaleIO copy_image_to_volume volume: %(vol)s image service: "
                 "%(service)s image id: %(id)s.",
                 {'vol': volume,
                  'service': six.text_type(image_service),
                  'id': six.text_type(image_id)})

        try:
            image_utils.fetch_to_raw(context,
                                     image_service,
                                     image_id,
                                     self._sio_attach_volume(volume),
                                     BLOCK_SIZE,
                                     size=volume['size'])

        finally:
            self._sio_detach_volume(volume)

    def copy_volume_to_image(self, context, volume, image_service, image_meta):
        """Copy the volume to the specified image."""
        LOG.info("ScaleIO copy_volume_to_image volume: %(vol)s image service: "
                 "%(service)s image meta: %(meta)s.",
                 {'vol': volume,
                  'service': six.text_type(image_service),
                  'meta': six.text_type(image_meta)})
        try:
            image_utils.upload_volume(context,
                                      image_service,
                                      image_meta,
                                      self._sio_attach_volume(volume))
        finally:
            self._sio_detach_volume(volume)

    def update_migrated_volume(self, ctxt, volume, new_volume,
                               original_volume_status):
        """Return the update from ScaleIO migrated volume.

        This method updates the volume name of the new ScaleIO volume to
        match the updated volume ID.
        The original volume is renamed first since ScaleIO does not allow
        multiple volumes to have the same name.
        """
        name_id = None
        location = None
        if original_volume_status == 'available':
            # During migration, a new volume is created and will replace
            # the original volume at the end of the migration. We need to
            # rename the new volume. The current_name of the new volume,
            # which is the id of the new volume, will be changed to the
            # new_name, which is the id of the original volume.
            current_name = new_volume['id']
            new_name = volume['id']
            vol_id = new_volume['provider_id']
            LOG.info("Renaming %(id)s from %(current_name)s to "
                     "%(new_name)s.",
                     {'id': vol_id, 'current_name': current_name,
                      'new_name': new_name})

            # Original volume needs to be renamed first
            self._rename_volume(volume, "ff" + new_name)
            self._rename_volume(new_volume, new_name)
        else:
            # The back-end will not be renamed.
            name_id = new_volume['_name_id'] or new_volume['id']
            location = new_volume['provider_location']

        return {'_name_id': name_id, 'provider_location': location}

    def _rename_volume(self, volume, new_id):
        new_name = self._id_to_base64(new_id)
        vol_id = volume['provider_id']

        req_vars = {'server_ip': self.server_ip,
                    'server_port': self.server_port,
                    'id': vol_id}
        request = ("https://%(server_ip)s:%(server_port)s"
                   "/api/instances/Volume::%(id)s/action/setVolumeName" %
                   req_vars)
        LOG.info("ScaleIO rename volume request: %s.", request)

        params = {'newName': new_name}
        r, response = self._execute_scaleio_post_request(params, request)

        if r.status_code != OK_STATUS_CODE:
            error_code = response['errorCode']
            if ((error_code == VOLUME_NOT_FOUND_ERROR or
                 error_code == OLD_VOLUME_NOT_FOUND_ERROR or
                 error_code == ILLEGAL_SYNTAX)):
                LOG.info("Ignoring renaming action because the volume "
                         "%(vol)s is not a ScaleIO volume.",
                         {'vol': vol_id})
            else:
                msg = (_("Error renaming volume %(vol)s: %(err)s.") %
                       {'vol': vol_id, 'err': response['message']})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
        else:
            LOG.info("ScaleIO volume %(vol)s was renamed to "
                     "%(new_name)s.",
                     {'vol': vol_id, 'new_name': new_name})

    def _query_scaleio_volume(self, volume, existing_ref):
        request = self._create_scaleio_get_volume_request(volume, existing_ref)
        r, response = self._execute_scaleio_get_request(request)
        LOG.info("Get Volume response: %(res)s",
                 {'res': response})
        self._manage_existing_check_legal_response(r, existing_ref)
        return response

    def manage_existing(self, volume, existing_ref):
        """Manage an existing ScaleIO volume.

        existing_ref is a dictionary of the form:
        {'source-id': <id of ScaleIO volume>}
        """
        response = self._query_scaleio_volume(volume, existing_ref)
        return {'provider_id': response['id']}

    def manage_existing_get_size(self, volume, existing_ref):
        return self._get_volume_size(volume, existing_ref)

    def manage_existing_snapshot(self, snapshot, existing_ref):
        """Manage an existing ScaleIO snapshot.

        :param snapshot: the snapshot to manage
        :param existing_ref: dictionary of the form:
            {'source-id': <id of ScaleIO snapshot>}
        """
        response = self._query_scaleio_volume(snapshot, existing_ref)
        not_real_parent = (response.get('orig_parent_overriden') or
                           response.get('is_source_deleted'))
        if not_real_parent:
            reason = (_("The snapshot's parent is not the original parent due "
                        "to deletion or revert action, therefore "
                        "this snapshot cannot be managed."))
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref,
                reason=reason
            )
        ancestor_id = response['ancestorVolumeId']
        volume_id = snapshot.volume.provider_id
        if ancestor_id != volume_id:
            reason = (_("The snapshot's parent in ScaleIO is %(ancestor)s "
                        "and not %(volume)s.") %
                      {'ancestor': ancestor_id, 'volume': volume_id})
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref,
                reason=reason
            )
        return {'provider_id': response['id']}

    def manage_existing_snapshot_get_size(self, snapshot, existing_ref):
        return self._get_volume_size(snapshot, existing_ref)

    def _get_volume_size(self, volume, existing_ref):
        response = self._query_scaleio_volume(volume, existing_ref)
        return int(math.ceil(float(response['sizeInKb']) / units.Mi))

    def _execute_scaleio_get_request(self, request):
        r = requests.get(
            request,
            auth=(
                self.server_username,
                self.server_token),
            verify=self._get_verify_cert())
        r = self._check_response(r, request)
        response = r.json()
        return r, response

    def _create_scaleio_get_volume_request(self, volume, existing_ref):
        """Throws an exception if the input is invalid for manage existing.

        if the input is valid - return a request.
        """
        type_id = volume.get('volume_type_id')
        if 'source-id' not in existing_ref:
            reason = _("Reference must contain source-id.")
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref,
                reason=reason
            )
        if type_id is None:
            reason = _("Volume must have a volume type")
            raise exception.ManageExistingVolumeTypeMismatch(
                existing_ref=existing_ref,
                reason=reason
            )
        vol_id = existing_ref['source-id']
        req_vars = {'server_ip': self.server_ip,
                    'server_port': self.server_port,
                    'id': vol_id}
        request = ("https://%(server_ip)s:%(server_port)s"
                   "/api/instances/Volume::%(id)s" % req_vars)
        LOG.info("ScaleIO get volume by id request: %s.", request)
        return request

    @staticmethod
    def _manage_existing_check_legal_response(response, existing_ref):
        if response.status_code != OK_STATUS_CODE:
            reason = (_("Error managing volume: %s.") % response.json()[
                'message'])
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref,
                reason=reason
            )

        if response.json()['mappedSdcInfo'] is not None:
            reason = _("manage_existing cannot manage a volume "
                       "connected to hosts. Please disconnect this volume "
                       "from existing hosts before importing.")
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref,
                reason=reason
            )

    def create_group(self, context, group):
        """Creates a group.

        :param context: the context of the caller.
        :param group: the group object.
        :returns: model_update

        ScaleIO won't create CG until cg-snapshot creation,
        db will maintain the volumes and CG relationship.
        """

        # let generic volume group support handle non-cgsnapshots
        if not volume_utils.is_group_a_cg_snapshot_type(group):
            raise NotImplementedError()

        LOG.info("Creating Group")
        model_update = {'status': fields.GroupStatus.AVAILABLE}
        return model_update

    def delete_group(self, context, group, volumes):
        """Deletes a group.

        :param context: the context of the caller.
        :param group: the group object.
        :param volumes: a list of volume objects in the group.
        :returns: model_update, volumes_model_update

        ScaleIO will delete the volumes of the CG.
        """

        # let generic volume group support handle non-cgsnapshots
        if not volume_utils.is_group_a_cg_snapshot_type(group):
            raise NotImplementedError()

        LOG.info("Deleting Group")
        model_update = {'status': fields.GroupStatus.DELETED}
        error_statuses = [fields.GroupStatus.ERROR,
                          fields.GroupStatus.ERROR_DELETING]
        volumes_model_update = []
        for volume in volumes:
            try:
                self._delete_volume(volume['provider_id'])
                update_item = {'id': volume['id'],
                               'status': 'deleted'}
                volumes_model_update.append(update_item)
            except exception.VolumeBackendAPIException as err:
                update_item = {'id': volume['id'],
                               'status': 'error_deleting'}
                volumes_model_update.append(update_item)
                if model_update['status'] not in error_statuses:
                    model_update['status'] = 'error_deleting'
                LOG.error("Failed to delete the volume %(vol)s of group. "
                          "Exception: %(exception)s.",
                          {'vol': volume['name'], 'exception': err})
        return model_update, volumes_model_update

    def create_group_snapshot(self, context, group_snapshot, snapshots):
        """Creates a group snapshot.

        :param context: the context of the caller.
        :param group_snapshot: the GroupSnapshot object to be created.
        :param snapshots: a list of Snapshot objects in the group_snapshot.
        :returns: model_update, snapshots_model_update
        """

        # let generic volume group support handle non-cgsnapshots
        if not volume_utils.is_group_a_cg_snapshot_type(group_snapshot):
            raise NotImplementedError()

        get_scaleio_snapshot_params = lambda snapshot: {
            'volumeId': snapshot.volume['provider_id'],
            'snapshotName': self._id_to_base64(snapshot['id'])}
        snapshot_defs = list(map(get_scaleio_snapshot_params, snapshots))
        r, response = self._snapshot_volume_group(snapshot_defs)
        LOG.info("Snapshot volume response: %s.", response)
        if r.status_code != OK_STATUS_CODE and "errorCode" in response:
            msg = (_("Failed creating snapshot for group: "
                     "%(response)s.") %
                   {'response': response['message']})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)
        snapshot_model_update = []
        for snapshot, scaleio_id in zip(snapshots, response['volumeIdList']):
            update_item = {'id': snapshot['id'],
                           'status': fields.SnapshotStatus.AVAILABLE,
                           'provider_id': scaleio_id}
            snapshot_model_update.append(update_item)
        model_update = {'status': fields.GroupStatus.AVAILABLE}
        return model_update, snapshot_model_update

    def delete_group_snapshot(self, context, group_snapshot, snapshots):
        """Deletes a snapshot.

        :param context: the context of the caller.
        :param group_snapshot: the GroupSnapshot object to be deleted.
        :param snapshots: a list of snapshot objects in the group_snapshot.
        :returns: model_update, snapshots_model_update
        """

        # let generic volume group support handle non-cgsnapshots
        if not volume_utils.is_group_a_cg_snapshot_type(group_snapshot):
            raise NotImplementedError()

        error_statuses = [fields.SnapshotStatus.ERROR,
                          fields.SnapshotStatus.ERROR_DELETING]
        model_update = {'status': group_snapshot['status']}
        snapshot_model_update = []
        for snapshot in snapshots:
            try:
                self._delete_volume(snapshot.provider_id)
                update_item = {'id': snapshot['id'],
                               'status': fields.SnapshotStatus.DELETED}
                snapshot_model_update.append(update_item)
            except exception.VolumeBackendAPIException as err:
                update_item = {'id': snapshot['id'],
                               'status': fields.SnapshotStatus.ERROR_DELETING}
                snapshot_model_update.append(update_item)
                if model_update['status'] not in error_statuses:
                    model_update['status'] = (
                        fields.SnapshotStatus.ERROR_DELETING)
                LOG.error("Failed to delete the snapshot %(snap)s "
                          "of snapshot: %(snapshot_id)s. "
                          "Exception: %(exception)s.",
                          {'snap': snapshot['name'],
                           'exception': err,
                           'snapshot_id': group_snapshot.id})
        model_update['status'] = fields.GroupSnapshotStatus.DELETED
        return model_update, snapshot_model_update

    def create_group_from_src(self, context, group, volumes,
                              group_snapshot=None, snapshots=None,
                              source_group=None, source_vols=None):
        """Creates a group from source.

        :param context: the context of the caller.
        :param group: the Group object to be created.
        :param volumes: a list of Volume objects in the group.
        :param group_snapshot: the GroupSnapshot object as source.
        :param snapshots: a list of snapshot objects in group_snapshot.
        :param source_group: the Group object as source.
        :param source_vols: a list of volume objects in the source_group.
        :returns: model_update, volumes_model_update
        """

        # let generic volume group support handle non-cgsnapshots
        if not volume_utils.is_group_a_cg_snapshot_type(group):
            raise NotImplementedError()

        get_scaleio_snapshot_params = lambda src_volume, trg_volume: {
            'volumeId': src_volume['provider_id'],
            'snapshotName': self._id_to_base64(trg_volume['id'])}
        if group_snapshot and snapshots:
            snapshot_defs = map(get_scaleio_snapshot_params,
                                snapshots,
                                volumes)
        else:
            snapshot_defs = map(get_scaleio_snapshot_params,
                                source_vols,
                                volumes)
        r, response = self._snapshot_volume_group(list(snapshot_defs))
        LOG.info("Snapshot volume response: %s.", response)
        if r.status_code != OK_STATUS_CODE and "errorCode" in response:
            msg = (_("Failed creating snapshot for group: "
                     "%(response)s.") %
                   {'response': response['message']})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)
        volumes_model_update = []
        for volume, scaleio_id in zip(volumes, response['volumeIdList']):
            update_item = {'id': volume['id'],
                           'status': 'available',
                           'provider_id': scaleio_id}
            volumes_model_update.append(update_item)
        model_update = {'status': fields.GroupStatus.AVAILABLE}
        return model_update, volumes_model_update

    def update_group(self, context, group,
                     add_volumes=None, remove_volumes=None):
        """Update a  group.

        :param context: the context of the caller.
        :param group: the group object.
        :param add_volumes: a list of volume objects to be added.
        :param remove_volumes: a list of volume objects to be removed.
        :returns: model_update, add_volumes_update, remove_volumes_update

        ScaleIO does not handle volume grouping.
        Cinder maintains volumes and CG relationship.
        """

        if volume_utils.is_group_a_cg_snapshot_type(group):
            return None, None, None

        # we'll rely on the generic group implementation if it is not a
        # consistency group request.
        raise NotImplementedError()

    def _snapshot_volume_group(self, snapshot_defs):
        LOG.info("ScaleIO snapshot group of volumes")
        params = {'snapshotDefs': snapshot_defs}
        req_vars = {'server_ip': self.server_ip,
                    'server_port': self.server_port}
        request = ("https://%(server_ip)s:%(server_port)s"
                   "/api/instances/System/action/snapshotVolumes") % req_vars
        return self._execute_scaleio_post_request(params, request)

    def ensure_export(self, context, volume):
        """Driver entry point to get the export info for an existing volume."""
        pass

    def create_export(self, context, volume, connector):
        """Driver entry point to get the export info for a new volume."""
        pass

    def remove_export(self, context, volume):
        """Driver entry point to remove an export for a volume."""
        pass

    def check_for_export(self, context, volume_id):
        """Make sure volume is exported."""
        pass
