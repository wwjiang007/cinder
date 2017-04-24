#  Copyright (c) 2016 IBM Corporation
#  All Rights Reserved.
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
#
import ast
import eventlet
import six

from oslo_log import log as logging
from oslo_utils import excutils

from cinder import coordination
from cinder import exception
from cinder.i18n import _
import cinder.volume.drivers.ibm.ibm_storage as storage
from cinder.volume.drivers.ibm.ibm_storage import ds8k_helper as helper
from cinder.volume.drivers.ibm.ibm_storage import ds8k_restclient as restclient
from cinder.volume.drivers.ibm.ibm_storage import proxy

LOG = logging.getLogger(__name__)

PPRC_PATH_NOT_EXIST = 0x00
PPRC_PATH_HEALTHY = 0x01
PPRC_PATH_UNHEALTHY = 0x02
PPRC_PATH_FULL = 0x03


class MetroMirrorManager(object):
    """Manage metro mirror for replication."""

    def __init__(self, source, target):
        self._source = source
        self._target = target

    def switch_source_and_target(self):
        self._source, self._target = self._target, self._source

    def check_physical_links(self):
        ports = self._source.get_physical_links(
            self._target.backend['storage_wwnn'])
        if not ports:
            raise exception.VolumeDriverException(
                message=((_("%(tgt)s is not connected to %(src)s!") % {
                    'tgt': self._target.backend['storage_wwnn'],
                    'src': self._source.backend['storage_wwnn']
                })))

        pairs = [{
            'source_port_id': p['source_port_id'],
            'target_port_id': p['target_port_id']
        } for p in ports]
        if not self._target.backend['port_pairs']:
            # if there are more than eight physical links,
            # choose eight of them.
            self._target.backend['port_pairs'] = (
                pairs[:8] if len(pairs) > 8 else pairs)
        else:
            # verify the port pairs user set
            for pair in self._target.backend['port_pairs']:
                if pair not in pairs:
                    valid_pairs = ';'.join(
                        ["%s-%s" % (p['source_port_id'],
                                    p['target_port_id'])
                         for p in pairs])
                    invalid_pair = "%s-%s" % (pair['source_port_id'],
                                              pair['target_port_id'])
                    raise exception.VolumeDriverException(
                        message=((_("Invalid port pair: %(invalid)s, valid "
                                    "port pair(s) are: %(valid)s")
                                  % {'invalid': invalid_pair,
                                     'valid': valid_pairs})))
        self._source.backend['port_pairs'] = [{
            'source_port_id': p['target_port_id'],
            'target_port_id': p['source_port_id']
        } for p in self._target.backend['port_pairs']]

    def is_target_alive(self):
        try:
            self._target.get_systems()
        except restclient.TimeoutException as e:
            LOG.info("REST request time out, backend may be not available "
                     "any more. Exception: %s", e)
            return False

        return True

    def find_from_pprc_paths(self, specified_lss=None, excluded_lss=None):
        """find lss from existing pprc paths and pool id for it.

        the format of pool_lss_pair returned is as below:
        {'source': (pid, lss), 'target': (pid, lss)}
        """
        state, paths = self._filter_pprc_paths(specified_lss)
        if state != PPRC_PATH_HEALTHY:
            # check whether the physical links are available or not,
            # or have been changed.
            self.check_physical_links()
            return state, None
        if excluded_lss:
            paths = [p for p in paths
                     if p['source_lss_id'] not in excluded_lss]
        # only enable_replication will specify the source LSS
        # and it need to reuse LSS reserved for CG if this LSS
        # is in PPRC path.
        if not specified_lss:
            paths = [p for p in paths if p['source_lss_id'] not in
                     self._source.backend['lss_ids_for_cg']]

        # sort pairs according to the number of luns in their LSSes,
        # and get the pair which LSS has least luns.
        candidates = []
        source_lss_set = set(p['source_lss_id'] for p in paths)
        for lss in source_lss_set:
            # get the number of luns in source.
            src_luns = self._source.get_lun_number_in_lss(lss)
            if src_luns == helper.LSS_VOL_SLOTS and not specified_lss:
                continue

            spec_paths = [p for p in paths if p['source_lss_id'] == lss]
            for path in spec_paths:
                # get the number of luns in target.
                try:
                    tgt_luns = self._target.get_lun_number_in_lss(
                        path['target_lss_id'])
                except restclient.APIException:
                    # if DS8K can fix this problem, then remove the
                    # exception here.
                    LOG.error("Target LSS %s in PPRC path may doesn't "
                              "exist although PPRC path is available.",
                              path['target_lss_id'])
                    tgt_luns = 0
                candidates.append((path['source_lss_id'],
                                   path['target_lss_id'],
                                   src_luns + tgt_luns))
        if not candidates:
            return PPRC_PATH_FULL, None
        else:
            src_lss, tgt_lss, num = sorted(candidates, key=lambda c: c[2])[0]
            return PPRC_PATH_HEALTHY, {
                'source': (self._source.get_pool(src_lss), src_lss),
                'target': (self._target.get_pool(tgt_lss), tgt_lss)
            }

    def _filter_pprc_paths(self, lss):
        paths = self._source.get_pprc_paths(lss)
        if paths:
            # get the paths only connected to replication target
            paths = [p for p in paths if p['target_system_wwnn'] in
                     self._target.backend['storage_wwnn']]
        else:
            LOG.info("No PPRC paths found in primary DS8K.")
            return PPRC_PATH_NOT_EXIST, None

        # get the paths whose port pairs have been set in configuration file.
        expected_port_pairs = [(p['source_port_id'], p['target_port_id'])
                               for p in self._target.backend['port_pairs']]
        for path in paths[:]:
            port_pairs = [(p['source_port_id'], p['target_port_id'])
                          for p in path['port_pairs']]
            if not (set(port_pairs) & set(expected_port_pairs)):
                paths.remove(path)
        if not paths:
            LOG.info("Existing PPRC paths do not use port pairs that "
                     "are set.")
            return PPRC_PATH_NOT_EXIST, None

        # abandon PPRC paths according to volume type(fb/ckd)
        source_lss_set = set(p['source_lss_id'] for p in paths)
        if self._source.backend.get('device_mapping'):
            source_lss_set = source_lss_set & set(
                self._source.backend['device_mapping'].keys())
        else:
            all_lss = self._source.get_all_lss(['id', 'type'])
            fb_lss = set(
                lss['id'] for lss in all_lss if lss['type'] == 'fb')
            source_lss_set = source_lss_set & fb_lss
        paths = [p for p in paths if p['source_lss_id'] in source_lss_set]
        if not paths:
            LOG.info("No source LSS in PPRC paths has correct volume type.")
            return PPRC_PATH_NOT_EXIST, None

        # if the group property of lss doesn't match pool node,
        # abandon these paths.
        discarded_src_lss = []
        discarded_tgt_lss = []
        for lss in source_lss_set:
            spec_paths = [p for p in paths if p['source_lss_id'] == lss]
            if self._source.get_pool(lss) is None:
                discarded_src_lss.append(lss)
                continue

            for spec_path in spec_paths:
                tgt_lss = spec_path['target_lss_id']
                if self._target.get_pool(tgt_lss) is None:
                    discarded_tgt_lss.append(tgt_lss)

        if discarded_src_lss:
            paths = [p for p in paths if p['source_lss_id'] not in
                     discarded_src_lss]
        if discarded_tgt_lss:
            paths = [p for p in paths if p['target_lss_id'] not in
                     discarded_tgt_lss]
        if not paths:
            LOG.info("No PPRC paths can be re-used.")
            return PPRC_PATH_NOT_EXIST, None

        # abandon unhealthy PPRC paths.
        for path in paths[:]:
            failed_port_pairs = [
                p for p in path['port_pairs'] if p['state'] != 'success']
            if len(failed_port_pairs) == len(path['port_pairs']):
                paths.remove(path)
        if not paths:
            LOG.info("PPRC paths between primary and target DS8K "
                     "are unhealthy.")
            return PPRC_PATH_UNHEALTHY, None

        return PPRC_PATH_HEALTHY, paths

    def create_pprc_path(self, pool_lss_pair):
        src_lss = pool_lss_pair['source'][1]
        tgt_lss = pool_lss_pair['target'][1]
        # check whether the pprc path exists and is healthy or not firstly.
        pid = (self._source.backend['storage_wwnn'] + '_' + src_lss + ':' +
               self._target.backend['storage_wwnn'] + '_' + tgt_lss)
        state = self._is_pprc_paths_healthy(pid)
        LOG.info("The state of PPRC path %(path)s is %(state)s.",
                 {'path': pid, 'state': state})
        if state == PPRC_PATH_HEALTHY:
            return

        # create the pprc path
        pathData = {
            'target_system_wwnn': self._target.backend['storage_wwnn'],
            'source_lss_id': src_lss,
            'target_lss_id': tgt_lss,
            'port_pairs': self._target.backend['port_pairs']
        }
        LOG.info("PPRC path %(src)s:%(tgt)s will be created.",
                 {'src': src_lss, 'tgt': tgt_lss})
        self._source.create_pprc_path(pathData)

        # check the state of the pprc path
        LOG.debug("Checking the state of the new PPRC path.")
        for retry in range(4):
            eventlet.sleep(2)
            if self._is_pprc_paths_healthy(pid) == PPRC_PATH_HEALTHY:
                break
            if retry == 3:
                self._source.delete_pprc_path(pid)
                raise restclient.APIException(
                    data=(_("Failed to create PPRC path %(src)s:%(tgt)s.")
                          % {'src': src_lss, 'tgt': tgt_lss}))
        LOG.debug("Create the new PPRC path successfully.")

    def _is_pprc_paths_healthy(self, path_id):
        try:
            path = self._source.get_pprc_path(path_id)
        except restclient.APIException:
            return PPRC_PATH_NOT_EXIST

        for port in path['port_pairs']:
            if port['state'] == 'success':
                return PPRC_PATH_HEALTHY

        return PPRC_PATH_UNHEALTHY

    def create_pprc_pairs(self, lun):
        tgt_vol_id = lun.replication_driver_data[
            self._target.backend['id']]['vol_hex_id']
        tgt_stg_id = self._target.backend['storage_unit']

        vol_pairs = [{
            'source_volume': lun.ds_id,
            'source_system_id': self._source.backend['storage_unit'],
            'target_volume': tgt_vol_id,
            'target_system_id': tgt_stg_id
        }]
        pairData = {
            "volume_pairs": vol_pairs,
            "type": "metro_mirror",
            "options": ["permit_space_efficient_target",
                        "initial_copy_full"]
        }
        LOG.debug("Creating pprc pair, pairData is %s.", pairData)
        self._source.create_pprc_pair(pairData)
        self._source.wait_pprc_copy_finished([lun.ds_id], 'full_duplex')
        LOG.info("The state of PPRC pair has become full_duplex.")

    def delete_pprc_pairs(self, lun):
        self._source.delete_pprc_pair(lun.ds_id)
        if self.is_target_alive() and lun.replication_driver_data:
            replica = sorted(lun.replication_driver_data.values())[0]
            self._target.delete_pprc_pair(replica['vol_hex_id'])

    def do_pprc_failover(self, luns, backend_id):
        vol_pairs = []
        target_vol_ids = []
        for lun in luns:
            target_vol_id = (
                lun.replication_driver_data[backend_id]['vol_hex_id'])
            if not self._target.lun_exists(target_vol_id):
                LOG.info("Target volume %(volid)s doesn't exist in "
                         "DS8K %(storage)s.",
                         {'volid': target_vol_id,
                          'storage': self._target.backend['storage_unit']})
                continue

            vol_pairs.append({
                'source_volume': target_vol_id,
                'source_system_id': self._target.backend['storage_unit'],
                'target_volume': lun.ds_id,
                'target_system_id': self._source.backend['storage_unit']
            })
            target_vol_ids.append(target_vol_id)

        pairData = {
            "volume_pairs": vol_pairs,
            "type": "metro_mirror",
            "options": ["failover"]
        }

        LOG.info("Begin to fail over to %s",
                 self._target.backend['storage_unit'])
        self._target.create_pprc_pair(pairData)
        self._target.wait_pprc_copy_finished(target_vol_ids,
                                             'suspended', False)
        LOG.info("Failover from %(src)s to %(tgt)s is finished.", {
            'src': self._source.backend['storage_unit'],
            'tgt': self._target.backend['storage_unit']
        })

    def do_pprc_failback(self, luns, backend_id):
        pprc_ids = []
        vol_ids = []
        for lun in luns:
            target_vol_id = (
                lun.replication_driver_data[backend_id]['vol_hex_id'])
            if not self._target.lun_exists(target_vol_id):
                LOG.info("Target volume %(volume)s doesn't exist in "
                         "DS8K %(storage)s.",
                         {'volume': lun.ds_id,
                          'storage': self._target.backend['storage_unit']})
                continue

            pprc_id = (self._source.backend['storage_unit'] + '_' +
                       lun.ds_id + ':' +
                       self._target.backend['storage_unit'] +
                       '_' + target_vol_id)
            pprc_ids.append(pprc_id)
            vol_ids.append(lun.ds_id)

        pairData = {"pprc_ids": pprc_ids,
                    "type": "metro_mirror",
                    "options": ["failback"]}

        LOG.info("Begin to run failback in %s.",
                 self._source.backend['storage_unit'])
        self._source.do_failback(pairData)
        self._source.wait_pprc_copy_finished(vol_ids, 'full_duplex', False)
        LOG.info("Run failback in %s is finished.",
                 self._source.backend['storage_unit'])


class Replication(object):
    """Metro Mirror and Global Mirror will be used by it."""

    def __init__(self, source_helper, target_device):
        self._source_helper = source_helper
        connection_type = target_device.get('connection_type')
        if connection_type == storage.XIV_CONNECTION_TYPE_FC:
            self._target_helper = (
                helper.DS8KReplicationTargetHelper(target_device))
        elif connection_type == storage.XIV_CONNECTION_TYPE_FC_ECKD:
            self._target_helper = (
                helper.DS8KReplicationTargetECKDHelper(target_device))
        else:
            raise exception.InvalidParameterValue(
                err=(_("Param [connection_type] %s in replication_device "
                       "is invalid.") % connection_type))

        self._target_helper.backend['lss_ids_for_cg'] = (
            self._source_helper.backend['lss_ids_for_cg'])
        self._mm_manager = MetroMirrorManager(self._source_helper,
                                              self._target_helper)

    def check_connection_type(self):
        src_conn_type = self._source_helper.get_connection_type()
        tgt_conn_type = self._target_helper.get_connection_type()
        if src_conn_type != tgt_conn_type:
            raise exception.VolumeDriverException(
                message=(_("The connection type in primary backend is "
                           "%(primary)s, but in secondary backend it is "
                           "%(secondary)s")
                         % {'primary': src_conn_type,
                            'secondary': tgt_conn_type}))
        # PPRC can not copy from ESE volume to standard volume or vice versus.
        if src_conn_type == storage.XIV_CONNECTION_TYPE_FC_ECKD:
            src_thin = self._source_helper.get_thin_provision()
            tgt_thin = self._target_helper.get_thin_provision()
            if src_thin != tgt_thin:
                self._source_helper.disable_thin_provision()
                self._target_helper.disable_thin_provision()

    def check_physical_links(self):
        self._mm_manager.check_physical_links()

    def switch_source_and_target(self, secondary_id, luns=None):
        # switch the helper in metro mirror manager
        self._mm_manager.switch_source_and_target()
        # switch the helper
        self._source_helper, self._target_helper = (
            self._target_helper, self._source_helper)
        # switch the volume id
        if luns:
            for lun in luns:
                backend = lun.replication_driver_data.get(secondary_id, None)
                lun.replication_driver_data.update(
                    {secondary_id: {'vol_hex_id': lun.ds_id}})
                lun.ds_id = backend['vol_hex_id']
        return luns

    @proxy.logger
    def find_pool_lss_pair(self, excluded_lss):
        state, pool_lss_pair = (
            self._mm_manager.find_from_pprc_paths(None, excluded_lss))
        if pool_lss_pair is None:
            pool_lss_pair = self.find_new_lss_for_source(excluded_lss)
            pool_lss_pair.update(self.find_new_lss_for_target())
        return pool_lss_pair

    @proxy.logger
    def find_new_lss_for_source(self, excluded_lss):
        src_pid, src_lss = self._source_helper.find_pool_and_lss(excluded_lss)
        return {'source': (src_pid, src_lss)}

    @proxy.logger
    def find_new_lss_for_target(self):
        tgt_pid, tgt_lss = self._target_helper.find_pool_and_lss()
        return {'target': (tgt_pid, tgt_lss)}

    @proxy.logger
    def enable_replication(self, lun, delete_source=False):
        state, lun.pool_lss_pair = (
            self._mm_manager.find_from_pprc_paths(lun.ds_id[0:2]))
        LOG.debug("enable_replication: pool_lss_pair is %s.",
                  lun.pool_lss_pair)
        if state == PPRC_PATH_UNHEALTHY:
            raise restclient.APIException(
                data=(_("The path(s) for volume %(name)s isn't available "
                        "any more, please make sure the state of the path(s) "
                        "which source LSS is %(lss)s is success.")
                      % {'name': lun.cinder_name, 'lss': lun.ds_id[0:2]}))
        elif state == PPRC_PATH_NOT_EXIST:
            pid = self._source_helper.get_pool(lun.ds_id[0:2])
            lun.pool_lss_pair = {'source': (pid, lun.ds_id[0:2])}
            lun.pool_lss_pair.update(self.find_new_lss_for_target())
        lun = self.create_replica(lun, delete_source)
        return lun

    @proxy.logger
    @coordination.synchronized('ibm-ds8k-replication')
    def create_replica(self, lun, delete_source=True):
        try:
            self._target_helper.create_lun(lun)
            # create PPRC paths if need.
            self._mm_manager.create_pprc_path(lun.pool_lss_pair)
            # create pprc pair
            self._mm_manager.create_pprc_pairs(lun)
        except restclient.APIException:
            with excutils.save_and_reraise_exception():
                self.delete_replica(lun)
                if delete_source:
                    self._source_helper.delete_lun(lun)
        lun.replication_status = 'enabled'
        return lun

    @proxy.logger
    def delete_replica(self, lun):
        if lun.ds_id is not None:
            try:
                self._mm_manager.delete_pprc_pairs(lun)
                self._delete_replica(lun)
            except restclient.APIException as e:
                raise exception.VolumeDriverException(
                    message=(_('Failed to delete the target volume for '
                               'volume %(volume)s, Exception: %(ex)s.')
                             % {'volume': lun.ds_id, 'ex': six.text_type(e)}))
        lun.replication_status = 'disabled'
        lun.replication_driver_data = {}
        return lun

    @proxy.logger
    def _delete_replica(self, lun):
        if not lun.replication_driver_data:
            LOG.error("No replica ID for lun %s, maybe there is something "
                      "wrong when creating the replica for lun.", lun.ds_id)
            return None

        for backend_id, backend in lun.replication_driver_data.items():
            if not self._mm_manager.is_target_alive():
                return None

            if not self._target_helper.lun_exists(backend['vol_hex_id']):
                LOG.debug("Replica %s not found.", backend['vol_hex_id'])
                continue

            LOG.debug("Deleting replica %s.", backend['vol_hex_id'])
            self._target_helper.delete_lun_by_id(backend['vol_hex_id'])

    def extend_replica(self, lun, param):
        for backend_id, backend in lun.replication_driver_data.items():
            self._target_helper.change_lun(backend['vol_hex_id'], param)

    def delete_pprc_pairs(self, lun):
        self._mm_manager.delete_pprc_pairs(lun)

    def create_pprc_pairs(self, lun):
        self._mm_manager.create_pprc_pairs(lun)

    def do_pprc_failover(self, luns, backend_id):
        self._mm_manager.do_pprc_failover(luns, backend_id)

    @proxy.logger
    def start_pprc_failback(self, luns, backend_id):
        # check whether primary client is alive or not.
        if not self._mm_manager.is_target_alive():
            try:
                self._target_helper.update_client()
            except restclient.APIException:
                msg = _("Can not connect to the primary backend, "
                        "please make sure it is back.")
                LOG.error(msg)
                raise exception.UnableToFailOver(reason=msg)

        LOG.debug("Failback starts, backend id is %s.", backend_id)
        for lun in luns:
            self._mm_manager.create_pprc_path(lun.pool_lss_pair)
        self._mm_manager.do_pprc_failback(luns, backend_id)
        # revert the relationship of source volume and target volume
        self.do_pprc_failover(luns, backend_id)
        self.switch_source_and_target(backend_id, luns)
        self._mm_manager.do_pprc_failback(luns, backend_id)
        LOG.debug("Failback ends, backend id is %s.", backend_id)

    @proxy.logger
    def failover_unreplicated_volume(self, lun):
        provider_location = ast.literal_eval(lun.volume['provider_location'])
        if 'old_status' in provider_location:
            updates = {'status': provider_location['old_status']}
            del provider_location['old_status']
            updates['provider_location'] = six.text_type(provider_location)
        else:
            provider_location['old_status'] = lun.status
            updates = {
                'status': 'error',
                'provider_location': six.text_type(provider_location)
            }
        return {'volume_id': lun.os_id, 'updates': updates}
