#
# Copyright (c) 2016 NEC Corporation.
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

import random
import six
import traceback

from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units

from cinder import coordination
from cinder import exception
from cinder.i18n import _
from cinder import volume
from cinder.volume.drivers.nec import cli
from cinder.volume.drivers.nec import volume_common


LOG = logging.getLogger(__name__)


class MStorageDriver(object):
    """M-Series Storage helper class."""

    VERSION = '1.8.1'
    WIKI_NAME = 'NEC_Cinder_CI'

    def _set_config(self, configuration, host, driver_name):
        self._configuration = configuration
        self._host = host
        self._driver_name = driver_name
        self._common = volume_common.MStorageVolumeCommon(configuration,
                                                          host,
                                                          driver_name)
        self._properties = self._common.get_conf_properties()
        self._cli = self._properties['cli']
        self._volume_api = volume.API()
        self._numofld_per_pool = 1024

    def do_setup(self, context):
        self._context = context
        self._common.set_context(self._context)

    def check_for_setup_error(self):
        if len(getattr(self._common._local_conf, 'nec_pools', [])) == 0:
            raise exception.ParameterNotFound(param='nec_pools')

    def _convert_id2name(self, volume):
        ldname = (self._common.get_ldname(volume['id'],
                                          self._properties['ld_name_format']))
        return ldname

    def _convert_id2snapname(self, volume):
        ldname = (self._common.get_ldname(volume['id'],
                                          self._properties[
                                              'ld_backupname_format']))
        return ldname

    def _convert_id2migratename(self, volume):
        ldname = self._convert_id2name(volume)
        ldname = ldname + '_m'
        return ldname

    def _convert_id2name_in_migrate(self, volume):
        """If LD has migrate_status, get LD name from source LD UUID."""
        LOG.debug('migration_status:%s', volume['migration_status'])
        migstat = volume['migration_status']
        if migstat is not None and 'target:' in migstat:
            index = migstat.find('target:')
            if index != -1:
                migstat = migstat[len('target:'):]
            ldname = (self._common.get_ldname(migstat,
                                              self._properties[
                                                  'ld_name_format']))
        else:
            ldname = (self._common.get_ldname(volume['id'],
                                              self._properties[
                                                  'ld_name_format']))

        LOG.debug('ldname=%s.', ldname)
        return ldname

    def _select_ldnumber(self, used_ldns, max_ld_count):
        """Pick up unused LDN."""
        for ldn in range(0, max_ld_count + 1):
            if ldn not in used_ldns:
                break
        if ldn > max_ld_count - 1:
            msg = _('All Logical Disk Numbers are used. '
                    'No more volumes can be created.')
            raise exception.VolumeBackendAPIException(data=msg)
        return ldn

    def _return_poolnumber(self, nominated_pools):
        """Select pool form nominated pools."""
        selected_pool = -1
        min_ldn = 0
        for pool in nominated_pools:
            nld = len(pool['ld_list'])
            if (nld < self._numofld_per_pool and
                    ((selected_pool == -1) or (min_ldn > nld))):
                selected_pool = pool['pool_num']
                min_ldn = nld
        if selected_pool < 0:
            msg = _('No available pools found.')
            raise exception.VolumeBackendAPIException(data=msg)
        return selected_pool

    def _select_leastused_poolnumber(self, volume, pools,
                                     xml, option=None):
        """Pick up least used pool."""
        size = volume['size'] * units.Gi
        pools = [pool for (pn, pool) in six.iteritems(pools)
                 if pool['free'] >= size and
                 (len(self._properties['pool_pools']) == 0 or
                  pn in self._properties['pool_pools'])]
        return self._return_poolnumber(pools)

    def _select_migrate_poolnumber(self, volume, pools, xml, option):
        """Pick up migration target pool."""
        tmpPools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))
        ldname = self._common.get_ldname(volume['id'],
                                         self._properties['ld_name_format'])
        ld = lds[ldname]
        temp_conf_properties = self._common.get_conf_properties(option)

        size = volume['size'] * units.Gi
        pools = [pool for (pn, pool) in six.iteritems(pools)
                 if pool['free'] >= size and
                 (len(temp_conf_properties['pool_pools']) == 0 or
                  pn in temp_conf_properties['pool_pools'])]

        selected_pool = self._return_poolnumber(pools)
        if selected_pool == ld['pool_num']:
            # it is not necessary to create new volume.
            selected_pool = -1
        return selected_pool

    def _select_dsv_poolnumber(self, volume, pools, option=None):
        """Pick up backup pool for DSV."""
        pools = [pool for (pn, pool) in six.iteritems(pools)
                 if pn in self._properties['pool_backup_pools']]
        return self._return_poolnumber(pools)

    def _select_ddr_poolnumber(self, volume, pools, xml, option):
        """Pick up backup pool for DDR."""
        size = option * units.Gi
        pools = [pool for (pn, pool) in six.iteritems(pools)
                 if pool['free'] >= size and
                 (pn in self._properties['pool_backup_pools'])]
        return self._return_poolnumber(pools)

    def _select_volddr_poolnumber(self, volume, pools, xml, option):
        """Pick up backup pool for DDR."""
        size = option * units.Gi
        pools = [pool for (pn, pool) in six.iteritems(pools)
                 if pool['free'] >= size and
                 (pn in self._properties['pool_pools'])]
        return self._return_poolnumber(pools)

    def _bind_ld(self, volume, capacity, validator,
                 nameselector, poolselector, option=None):
        return self._sync_bind_ld(volume, capacity, validator,
                                  nameselector, poolselector,
                                  self._properties['diskarray_name'],
                                  option)

    @coordination.synchronized('mstorage_bind_execute_{diskarray_name}')
    def _sync_bind_ld(self, volume, capacity, validator, nameselector,
                      poolselector, diskarray_name, option=None):
        """Get storage state and bind ld.

        volume: ld information
        capacity: capacity in GB
        validator: validate method(volume, xml)
        nameselector: select ld name method(volume)
        poolselector: select ld location method(volume, pools)
        diskarray_name: target diskarray name
        option: optional info
        """
        LOG.debug('_bind_ld Start.')
        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # execute validator function.
        if validator is not None:
            result = validator(volume, xml)
            if result is False:
                msg = _('Invalid bind Logical Disk info.')
                raise exception.VolumeBackendAPIException(data=msg)

        # generate new ld name.
        ldname = nameselector(volume)
        # pick up least used pool and unused LDN.
        selected_pool = poolselector(volume, pools, xml, option)
        selected_ldn = self._select_ldnumber(used_ldns, max_ld_count)
        if selected_pool < 0 or selected_ldn < 0:
            LOG.debug('NOT necessary LD bind. '
                      'Name=%(name)s '
                      'Size=%(size)dGB '
                      'LDN=%(ldn)04xh '
                      'Pool=%(pool)04xh.',
                      {'name': ldname,
                       'size': capacity,
                       'ldn': selected_ldn,
                       'pool': selected_pool})
            return ldname, selected_ldn, selected_pool

        # bind LD.
        retnum, errnum = (self._cli.ldbind(ldname,
                                           selected_pool,
                                           selected_ldn,
                                           capacity))
        if retnum is False:
            if 'iSM31077' in errnum:
                msg = _('Logical Disk number is duplicated (%s).') % errnum
                raise exception.VolumeBackendAPIException(data=msg)
            else:
                msg = _('Failed to bind Logical Disk (%s).') % errnum
                raise exception.VolumeBackendAPIException(data=msg)

        LOG.debug('LD bound. Name=%(name)s Size=%(size)dGB '
                  'LDN=%(ldn)04xh Pool=%(pool)04xh.',
                  {'name': ldname, 'size': capacity,
                   'ldn': selected_ldn, 'pool': selected_pool})
        return ldname, selected_ldn, selected_pool

    def create_volume(self, volume):
        msgparm = ('Volume ID = %(id)s, Size = %(size)dGB'
                   % {'id': volume['id'], 'size': volume['size']})
        try:
            self._create_volume(volume)
            LOG.info('Created Volume (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create Volume (%(msgparm)s) '
                            '(%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _create_volume(self, volume):
        LOG.debug('_create_volume Start.')

        # select ld number and LD bind.
        (ldname,
         ldn,
         selected_pool) = self._bind_ld(volume,
                                        volume['size'],
                                        None,
                                        self._convert_id2name_in_migrate,
                                        self._select_leastused_poolnumber)

        # check io limit.
        specs = self._common.get_volume_type_qos_specs(volume)
        self._common.check_io_parameter(specs)
        # set io limit.
        self._cli.set_io_limit(ldname, specs)

        LOG.debug('LD bound. '
                  'Name=%(name)s '
                  'Size=%(size)dGB '
                  'LDN=%(ldn)04xh '
                  'Pool=%(pool)04xh '
                  'Specs=%(specs)s.',
                  {'name': ldname,
                   'size': volume['size'],
                   'ldn': ldn,
                   'pool': selected_pool,
                   'specs': specs})

    def _can_extend_capacity(self, new_size, pools, lds, ld):
        rvs = {}
        ld_count_in_pool = {}
        if ld['RPL Attribute'] == 'MV':
            pair_lds = self._cli.get_pair_lds(ld['ldname'], lds)
            for (ldn, pair_ld) in six.iteritems(pair_lds):
                rv_name = pair_ld['ldname']
                pool_number = pair_ld['pool_num']
                ldn = pair_ld['ldn']
                rvs[ldn] = pair_ld
                # check rv status.
                query_status = self._cli.query_MV_RV_status(rv_name[3:], 'RV')
                if query_status != 'separated':
                    msg = (_('Specified Logical Disk %s has been copied.') %
                           rv_name)
                    LOG.error(msg)
                    raise exception.VolumeBackendAPIException(data=msg)
                # get pool number.
                if pool_number in ld_count_in_pool:
                    ld_count_in_pool[pool_number].append(ldn)
                else:
                    ld_count_in_pool[pool_number] = [ldn]

        # check pool capacity.
        for (pool_number, tmp_ldn_list) in six.iteritems(ld_count_in_pool):
            ld_capacity = (
                ld['ld_capacity'] * units.Gi)
            new_size_byte = new_size * units.Gi
            size_increase = new_size_byte - ld_capacity
            pool = pools[pool_number]
            ld_count = len(tmp_ldn_list)
            if pool['free'] < size_increase * ld_count:
                msg = (_('Not enough pool capacity. '
                         'pool_number=%(pool)d, size_increase=%(sizeinc)d') %
                       {'pool': pool_number,
                        'sizeinc': size_increase * ld_count})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        return rvs

    def extend_volume(self, volume, new_size):
        msgparm = ('Volume ID = %(id)s, New Size = %(newsize)dGB, '
                   'Old Size = %(oldsize)dGB'
                   % {'id': volume['id'], 'newsize': new_size,
                      'oldsize': volume['size']})
        try:
            self._extend_volume(volume, new_size)
            LOG.info('Extended Volume (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Extend Volume (%(msgparm)s) '
                            '(%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _extend_volume(self, volume, new_size):
        LOG.debug('_extend_volume(Volume ID = %(id)s, '
                  'new_size = %(size)s) Start.',
                  {'id': volume['id'], 'size': new_size})

        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        ldname = self._common.get_ldname(volume['id'],
                                         self._properties['ld_name_format'])

        # get volume.
        if ldname not in lds:
            msg = (_('Logical Disk has unbound already '
                     '(name=%(name)s, id=%(id)s).') %
                   {'name': ldname, 'id': volume['id']})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)
        ld = lds[ldname]
        ldn = ld['ldn']

        # heck pools capacity.
        rvs = self._can_extend_capacity(new_size, pools, lds, ld)

        # volume expand.
        self._cli.expand(ldn, new_size)

        # rv expand.
        if ld['RPL Attribute'] == 'MV':
            # ld expand.
            for (ldn, rv) in six.iteritems(rvs):
                self._cli.expand(ldn, new_size)
        elif ld['RPL Attribute'] != 'IV':
            msg = (_('RPL Attribute Error. RPL Attribute = %s.')
                   % ld['RPL Attribute'])
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        LOG.debug('_extend_volume(Volume ID = %(id)s, '
                  'new_size = %(newsize)s) End.',
                  {'id': volume['id'], 'newsize': new_size})

    def create_cloned_volume(self, volume, src_vref):
        msgparm = ('Volume ID = %(id)s, '
                   'Source Volume ID = %(src_id)s'
                   % {'id': volume['id'],
                      'src_id': src_vref['id']})
        try:
            self._create_cloned_volume(volume, src_vref)
            LOG.info('Created Cloned Volume (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create Cloned Volume '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _create_cloned_volume(self, volume, src_vref):
        """Creates a clone of the specified volume."""
        LOG.debug('_create_cloned_volume'
                  '(Volume ID = %(id)s, Source ID = %(src_id)s ) Start.',
                  {'id': volume['id'], 'src_id': src_vref['id']})

        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # check MV existence and get MV info.
        source_name = (
            self._common.get_ldname(src_vref['id'],
                                    self._properties['ld_name_format']))
        if source_name not in lds:
            msg = (_('Logical Disk `%(name)s` has unbound already. '
                     'volume_id = %(id)s.') %
                   {'name': source_name, 'id': src_vref['id']})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)
        source_ld = lds[source_name]

        # check temporarily released pairs existence.
        if source_ld['RPL Attribute'] == 'MV':
            # get pair lds.
            pair_lds = self._cli.get_pair_lds(source_name, lds)
            if len(pair_lds) == 3:
                msg = (_('Cannot create clone volume. '
                         'number of pairs reached 3. '
                         '%(msg)s. ldname=%(ldname)s') %
                       {'msg': msg, 'ldname': source_name})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        # Creating Cloned Volume.
        (volume_name,
         ldn,
         selected_pool) = self._bind_ld(volume,
                                        src_vref['size'],
                                        None,
                                        self._convert_id2name,
                                        self._select_leastused_poolnumber)

        # check io limit.
        specs = self._common.get_volume_type_qos_specs(volume)
        self._common.check_io_parameter(specs)

        # set io limit.
        self._cli.set_io_limit(volume_name, specs)

        LOG.debug('LD bound. Name=%(name)s '
                  'Size=%(size)dGB '
                  'LDN=%(ldn)04xh '
                  'Pool=%(pool)04xh.',
                  {'name': volume_name,
                   'size': volume['size'],
                   'ldn': ldn,
                   'pool': selected_pool})
        LOG.debug('source_name=%(src_name)s, volume_name=%(name)s.',
                  {'src_name': source_name, 'name': volume_name})

        # compare volume size and copy data to RV.
        mv_capacity = src_vref['size']
        rv_capacity = volume['size']
        if rv_capacity <= mv_capacity:
            rv_capacity = None

        volume_properties = {
            'mvname': source_name,
            'rvname': volume_name,
            'capacity': mv_capacity,
            'mvid': src_vref['id'],
            'rvid': volume['id'],
            'rvldn': ldn,
            'rvcapacity': rv_capacity,
            'flag': 'clone',
            'context': self._context
        }
        self._cli.backup_restore(volume_properties, cli.UnpairWaitForClone)
        LOG.debug('_create_cloned_volume(Volume ID = %(id)s, '
                  'Source ID = %(src_id)s ) End.',
                  {'id': volume['id'], 'src_id': src_vref['id']})

    def _validate_migrate_volume(self, volume, xml):
        """Validate source volume information."""
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))
        ldname = self._common.get_ldname(volume['id'],
                                         self._properties['ld_name_format'])

        # check volume status.
        if volume['status'] != 'available':
            msg = _('Specified Logical Disk %s is not available.') % ldname
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        # get ld object and check rpl attribute.
        if ldname not in lds:
            msg = _('Logical Disk `%s` does not exist.') % ldname
            LOG.error(msg)
            raise exception.NotFound(msg)
        ld = lds[ldname]
        if ld['Purpose'] != '---':
            msg = (_('Specified Logical Disk %(ld)s '
                     'has an invalid attribute (%(purpose)s).')
                   % {'ld': ldname, 'purpose': ld['Purpose']})
            raise exception.VolumeBackendAPIException(data=msg)
        return True

    def migrate_volume(self, context, volume, host):
        msgparm = ('Volume ID = %(id)s, '
                   'Destination Host = %(dsthost)s'
                   % {'id': volume['id'],
                      'dsthost': host})
        try:
            ret = self._migrate_volume(context, volume, host)
            LOG.info('Migrated Volume (%s)', msgparm)
            return ret
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Migrate Volume '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _migrate_volume(self, context, volume, host):
        """Migrate the volume to the specified host.

        Returns a boolean indicating whether the migration occurred, as well as
        model_update.
        """
        LOG.debug('_migrate_volume('
                  'Volume ID = %(id)s, '
                  'Volume Name = %(name)s, '
                  'host = %(host)s) Start.',
                  {'id': volume['id'],
                   'name': volume['name'],
                   'host': host})

        false_ret = (False, None)

        if 'capabilities' not in host:
            LOG.debug('Host not in capabilities. Host = %s ', host)
            return false_ret

        capabilities = host['capabilities']
        if capabilities.get('vendor_name') != self._properties['vendor_name']:
            LOG.debug('Vendor is not %(vendor)s. '
                      'capabilities = %(capabilities)s ',
                      {'vendor': self._properties['vendor_name'],
                       'capabilities': capabilities})
            return false_ret

        # get another host group configurations.
        temp_conf = self._common.get_conf(host)
        temp_conf_properties = self._common.get_conf_properties(temp_conf)

        # another storage configuration is not supported.
        if temp_conf_properties['cli_fip'] != self._properties['cli_fip']:
            LOG.debug('FIP is mismatch. FIP = %(tempfip)s != %(fip)s',
                      {'tempfip': temp_conf_properties['cli_fip'],
                       'fip': self._properties['cli_fip']})
            return false_ret

        # bind LD.
        (rvname,
         ldn,
         selected_pool) = self._bind_ld(volume,
                                        volume['size'],
                                        self._validate_migrate_volume,
                                        self._convert_id2migratename,
                                        self._select_migrate_poolnumber,
                                        temp_conf)

        if selected_pool >= 0:
            # check io limit.
            specs = self._common.get_volume_type_qos_specs(volume)
            self._common.check_io_parameter(specs)

            # set io limit.
            self._cli.set_io_limit(rvname, specs)

            volume_properties = {
                'mvname':
                    self._common.get_ldname(
                        volume['id'], self._properties['ld_name_format']),
                'rvname': rvname,
                'capacity':
                    volume['size'] * units.Gi,
                'mvid': volume['id'],
                'rvid': None,
                'flag': 'migrate',
                'context': self._context
            }
            # replicate LD.
            self._cli.backup_restore(volume_properties,
                                     cli.UnpairWaitForMigrate)

        LOG.debug('_migrate_volume(Volume ID = %(id)s, '
                  'Host = %(host)s) End.',
                  {'id': volume['id'], 'host': host})

        return (True, [])

    def check_for_export(self, context, volume_id):
        pass

    def iscsi_do_export(self, _ctx, volume, connector, ensure=False):
        msgparm = ('Volume ID = %(id)s, '
                   'Initiator Name = %(initiator)s'
                   % {'id': volume['id'],
                      'initiator': connector['initiator']})
        try:
            ret = self._iscsi_do_export(_ctx, volume, connector, ensure)
            LOG.info('Created iSCSI Export (%s)', msgparm)
            return ret
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create iSCSI Export '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _iscsi_do_export(self, _ctx, volume, connector, ensure):
        while True:
            xml = self._cli.view_all(self._properties['ismview_path'])
            pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
                self._common.configs(xml))

            # find LD Set.

            # get target LD Set name.
            metadata = {}
            # image to volume or volume to image.
            if (volume['status'] in ['downloading', 'uploading'] and
                    self._properties['ldset_controller_node_name'] != ''):
                metadata['ldset'] = (
                    self._properties['ldset_controller_node_name'])
                LOG.debug('image to volume or volume to image:%s',
                          volume['status'])
            # migrate.
            elif (volume['migration_status'] is not None and
                    self._properties['ldset_controller_node_name'] != ''):
                metadata['ldset'] = (
                    self._properties['ldset_controller_node_name'])
                LOG.debug('migrate:%s', volume['migration_status'])

            ldset = self._common.get_ldset(ldsets, metadata)
            if ldset is None:
                for tldset in six.itervalues(ldsets):
                    n = tldset['initiator_list'].count(connector['initiator'])
                    if ('initiator_list' in tldset and n > 0):
                        ldset = tldset
                        LOG.debug('ldset=%s.', ldset)
                        break
                if ldset is None:
                    msg = _('Appropriate Logical Disk Set could not be found.')
                    raise exception.NotFound(msg)

            if len(ldset['portal_list']) < 1:
                msg = (_('Logical Disk Set `%s` has no portal.') %
                       ldset['ldsetname'])
                raise exception.NotFound(msg)

            LOG.debug('migration_status:%s', volume['migration_status'])
            migstat = volume['migration_status']
            if migstat is not None and 'target:' in migstat:
                index = migstat.find('target:')
                if index != -1:
                    migstat = migstat[len('target:'):]
                ldname = (
                    self._common.get_ldname(
                        migstat, self._properties['ld_name_format']))
            else:
                ldname = (
                    self._common.get_ldname(
                        volume['id'], self._properties['ld_name_format']))

            # add LD to LD set.
            if ldname not in lds:
                msg = _('Logical Disk `%s` could not be found.') % ldname
                raise exception.NotFound(msg)
            ld = lds[ldname]

            if ld['ldn'] not in ldset['lds']:
                # Check the LD is remaining on ldset_controller_node.
                ldset_controller_node_name = (
                    self._properties['ldset_controller_node_name'])
                if ldset_controller_node_name != '':
                    if ldset_controller_node_name != ldset['ldsetname']:
                        ldset_controller = ldsets[ldset_controller_node_name]
                        if ld['ldn'] in ldset_controller['lds']:
                            LOG.debug(
                                'delete remaining the LD from '
                                'ldset_controller_node. '
                                'Ldset Name=%s.',
                                ldset_controller_node_name)
                            self._cli.delldsetld(ldset_controller_node_name,
                                                 ldname)
                # assign the LD to LD Set.
                self._cli.addldsetld(ldset['ldsetname'], ldname)

                LOG.debug('Add LD `%(ld)s` to LD Set `%(ldset)s`.',
                          {'ld': ldname, 'ldset': ldset['ldsetname']})
            else:
                break

        # enumerate portals for iscsi multipath.
        prefered_director = ld['pool_num'] % 2
        nominated = []
        for director in [prefered_director, 1 - prefered_director]:
            if director not in hostports:
                continue
            dirportal = []
            for port in hostports[director]:
                if not port['protocol'] == 'iSCSI':
                    continue
                for portal in ldset['portal_list']:
                    if portal.startswith(port['ip'] + ':'):
                        dirportal.append(portal)
                        break
            if ((self._properties['portal_number'] > 0) and
                    (len(dirportal) > self._properties['portal_number'])):
                nominated.extend(random.sample(
                    dirportal, self._properties['portal_number']))
            else:
                nominated.extend(dirportal)

        if len(nominated) == 0:
            raise exception.NotFound(
                _('Any portal not match to any host ports.'))

        location = ('%(list)s,1 %(iqn)s %(lun)d'
                    % {'list': ';'.join(nominated),
                       'iqn': ldset['lds'][ld['ldn']]['iqn'],
                       'lun': ldset['lds'][ld['ldn']]['lun']})

        LOG.debug('%(ensure)sexport LD `%(name)s` via `%(location)s`.',
                  {'ensure': 'ensure_' if ensure else '',
                   'name': ldname,
                   'location': location})
        return {'provider_location': location}

    def fc_do_export(self, _ctx, volume, connector, ensure=False):
        msgparm = ('Volume ID = %(id)s, '
                   'Initiator WWPNs = %(wwpns)s'
                   % {'id': volume['id'],
                      'wwpns': connector['wwpns']})
        try:
            ret = self._fc_do_export(_ctx, volume, connector, ensure)
            LOG.info('Created FC Export (%s)', msgparm)
            return ret
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create FC Export '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _fc_do_export(self, _ctx, volume, connector, ensure):
        while True:
            xml = self._cli.view_all(self._properties['ismview_path'])
            pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
                self._common.configs(xml))

            # find LD Set.

            # get target LD Set.
            metadata = {}
            # image to volume or volume to image.
            if (volume['status'] in ['downloading', 'uploading'] and
                    self._properties['ldset_controller_node_name'] != ''):
                metadata['ldset'] = (
                    self._properties['ldset_controller_node_name'])
                LOG.debug('image to volume or volume to image:%s',
                          volume['status'])
            # migrate.
            elif (volume['migration_status'] is not None and
                    self._properties['ldset_controller_node_name'] != ''
                  ):
                metadata['ldset'] = (
                    self._properties['ldset_controller_node_name'])
                LOG.debug('migrate:%s', volume['migration_status'])

            ldset = self._common.get_ldset(ldsets, metadata)
            if ldset is None:
                for conect in connector['wwpns']:
                    length = len(conect)
                    findwwpn = '-'.join([conect[i:i + 4]
                                         for i in range(0, length, 4)])
                    findwwpn = findwwpn.upper()
                    for tldset in six.itervalues(ldsets):
                        if 'wwpn' in tldset and findwwpn in tldset['wwpn']:
                            ldset = tldset
                            LOG.debug('ldset=%s.', ldset)
                            break
                    if ldset is not None:
                        break
                if ldset is None:
                    msg = _('Logical Disk Set could not be found.')
                    raise exception.NotFound(msg)

            # get free lun.
            luns = []
            ldsetlds = ldset['lds']
            for ld in six.itervalues(ldsetlds):
                luns.append(ld['lun'])

            target_lun = 0
            for lun in sorted(luns):
                if target_lun < lun:
                    break
                target_lun += 1

            LOG.debug('migration_status:%s', volume['migration_status'])
            migstat = volume['migration_status']
            if migstat is not None and 'target:' in migstat:
                index = migstat.find('target:')
                if index != -1:
                    migstat = migstat[len('target:'):]
                ldname = (
                    self._common.get_ldname(
                        migstat,
                        self._properties['ld_name_format']))
            else:
                ldname = (
                    self._common.get_ldname(
                        volume['id'],
                        self._properties['ld_name_format']))

            # add LD to LD set.
            if ldname not in lds:
                msg = _('Logical Disk `%s` could not be found.') % ldname
                raise exception.NotFound(msg)
            ld = lds[ldname]

            if ld['ldn'] not in ldset['lds']:
                # Check the LD is remaining on ldset_controller_node.
                ldset_controller_node_name = (
                    self._properties['ldset_controller_node_name'])
                if ldset_controller_node_name != '':
                    if ldset_controller_node_name != ldset['ldsetname']:
                        ldset_controller = ldsets[ldset_controller_node_name]
                        if ld['ldn'] in ldset_controller['lds']:
                            LOG.debug(
                                'delete remaining the LD from '
                                'ldset_controller_node. '
                                'Ldset Name=%s.'
                                % ldset_controller_node_name)
                            self._cli.delldsetld(ldset_controller_node_name,
                                                 ldname)
                # assign the LD to LD Set.
                self._cli.addldsetld(ldset['ldsetname'], ldname, target_lun)

                LOG.debug('Add LD `%(ld)s` to LD Set `%(ldset)s`.',
                          {'ld': ldname, 'ldset': ldset['ldsetname']})
            else:
                break

        LOG.debug('%(ensure)sexport LD `%(ld)s`.',
                  {'ensure': 'ensure_' if ensure else '',
                   'ld': ldname})

    def remove_export(self, context, volume):
        msgparm = 'Volume ID = %s' % volume['id']
        try:
            self._remove_export(context, volume)
            LOG.info('Removed Export (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Remove Export '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _remove_export(self, context, volume):
        if (volume['status'] == 'uploading' and
                volume['attach_status'] == 'attached'):
            return
        else:
            LOG.debug('_remove_export Start.')
            xml = self._cli.view_all(self._properties['ismview_path'])
            pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
                self._common.configs(xml))

            # get target LD Set.
            metadata = {}
            # image to volume or volume to image.
            if (volume['status'] in ['downloading', 'uploading'] and
                    self._properties['ldset_controller_node_name'] != ''):
                metadata['ldset'] = (
                    self._properties['ldset_controller_node_name'])
                LOG.debug('image to volume or volume to image:%s',
                          volume['status'])
            # migrate.
            elif (volume['migration_status'] is not None and
                    self._properties['ldset_controller_node_name'] != ''
                  ):
                metadata['ldset'] = (
                    self._properties['ldset_controller_node_name'])
                LOG.debug('migrate:%s', volume['migration_status'])

            ldset = self._common.get_ldset(ldsets, metadata)

            LOG.debug('migration_status:%s', volume['migration_status'])
            migstat = volume['migration_status']
            if migstat is not None and 'target:' in migstat:
                index = migstat.find('target:')
                if index != -1:
                    migstat = migstat[len('target:'):]
                ldname = (
                    self._common.get_ldname(
                        migstat,
                        self._properties['ld_name_format']))
            else:
                ldname = (
                    self._common.get_ldname(
                        volume['id'],
                        self._properties['ld_name_format']))

            if ldname not in lds:
                LOG.debug('LD `%s` already unbound?' % ldname)
                return

            ld = lds[ldname]
            ldsetlist = []

            if ldset is None:
                for tldset in six.itervalues(ldsets):
                    if ld['ldn'] in tldset['lds']:
                        ldsetlist.append(tldset)
                        LOG.debug('ldset=%s.', tldset)
                if len(ldsetlist) == 0:
                    LOG.debug('LD `%s` already deleted from LD Set?',
                              ldname)
                    return
            else:
                if ld['ldn'] not in ldset['lds']:
                    LOG.debug('LD `%(ld)s` already deleted '
                              'from LD Set `%(ldset)s`?',
                              {'ld': ldname, 'ldset': ldset['ldsetname']})
                    return
                ldsetlist.append(ldset)

            # delete LD from LD set.
            for tagetldset in ldsetlist:
                retnum, errnum = (self._cli.delldsetld(
                    tagetldset['ldsetname'], ldname))

                if retnum is not True:
                    if 'iSM31065' in errnum:
                        LOG.debug(
                            'LD `%(ld)s` already deleted '
                            'from LD Set `%(ldset)s`?',
                            {'ld': ldname, 'ldset': tagetldset['ldsetname']})
                    else:
                        msg = (_('Failed to unregister Logical Disk from '
                                 'Logical Disk Set (%s)') % errnum)
                        raise exception.VolumeBackendAPIException(data=msg)
                LOG.debug('LD `%(ld)s` deleted from LD Set `%(ldset)s`.',
                          {'ld': ldname, 'ldset': tagetldset['ldsetname']})

            LOG.debug('_remove_export(Volume ID = %s) End.', volume['id'])

    def iscsi_initialize_connection(self, volume, connector):
        msgparm = ('Volume ID = %(id)s, Connector = %(connector)s'
                   % {'id': volume['id'], 'connector': connector})

        try:
            ret = self._iscsi_initialize_connection(volume, connector)
            LOG.info('Initialized iSCSI Connection (%s)', msgparm)
            return ret
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Initialize iSCSI Connection '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _iscsi_initialize_connection(self, volume, connector):
        """Initializes the connection and returns connection info.

        The iscsi driver returns a driver_volume_type of 'iscsi'.
        The format of the driver data is defined in _get_iscsi_properties.
        Example return value::

            {
                'driver_volume_type': 'iscsi'
                'data': {
                    'target_discovered': True,
                    'target_iqn': 'iqn.2010-10.org.openstack:volume-00000001',
                    'target_portal': '127.0.0.0.1:3260',
                    'volume_id': 1,
                    'access_mode': 'rw'
                }
            }

        """
        LOG.debug('_iscsi_initialize_connection'
                  '(Volume ID = %(id)s, connector = %(connector)s) Start.',
                  {'id': volume['id'], 'connector': connector})

        provider_location = volume['provider_location']
        provider_location = provider_location.split()
        info = {'driver_volume_type': 'iscsi',
                'data': {'target_portal': random.choice(
                    provider_location[0][0:-2].split(";")),
                    'target_iqn': provider_location[1],
                    'target_lun': int(provider_location[2]),
                    'target_discovered': False,
                    'volume_id': volume['id']}
                }
        if connector.get('multipath'):
            portals_len = len(provider_location[0][0:-2].split(";"))
            info['data'].update({'target_portals':
                                provider_location[0][0:-2].split(";"),
                                 'target_iqns': [provider_location[1]] *
                                portals_len,
                                 'target_luns': [int(provider_location[2])] *
                                portals_len})
        LOG.debug('_iscsi_initialize_connection'
                  '(Volume ID = %(id)s, connector = %(connector)s, '
                  'info = %(info)s) End.',
                  {'id': volume['id'],
                   'connector': connector,
                   'info': info})
        return info

    def iscsi_terminate_connection(self, volume, connector):
        msgparm = ('Volume ID = %(id)s, Connector = %(connector)s'
                   % {'id': volume['id'], 'connector': connector})

        try:
            ret = self._iscsi_terminate_connection(volume, connector)
            LOG.info('Terminated iSCSI Connection (%s)', msgparm)
            return ret
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Terminate iSCSI Connection '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _iscsi_terminate_connection(self, volume, connector):
        LOG.debug('execute _iscsi_terminate_connection'
                  '(Volume ID = %(id)s, connector = %(connector)s).',
                  {'id': volume['id'], 'connector': connector})

    def fc_initialize_connection(self, volume, connector):
        msgparm = ('Volume ID = %(id)s, Connector = %(connector)s'
                   % {'id': volume['id'], 'connector': connector})

        try:
            ret = self._fc_initialize_connection(volume, connector)
            LOG.info('Initialized FC Connection (%s)', msgparm)
            return ret
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Initialize FC Connection '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _fc_initialize_connection(self, volume, connector):
        """Initializes the connection and returns connection info.

        The  driver returns a driver_volume_type of 'fibre_channel'.
        The target_wwn can be a single entry or a list of wwns that
        correspond to the list of remote wwn(s) that will export the volume.
        Example return values:

            {
                'driver_volume_type': 'fibre_channel'
                'data': {
                    'target_discovered': True,
                    'target_lun': 1,
                    'target_wwn': '1234567890123',
                    'access_mode': 'rw'
                }
            }

            or

             {
                'driver_volume_type': 'fibre_channel'
                'data': {
                    'target_discovered': True,
                    'target_lun': 1,
                    'target_wwn': ['1234567890123', '0987654321321'],
                    'access_mode': 'rw'
                }
            }
        """

        LOG.debug('_fc_initialize_connection'
                  '(Volume ID = %(id)s, connector = %(connector)s) Start.',
                  {'id': volume['id'], 'connector': connector})

        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # get target wwpns and initiator/target map.

        fc_ports = []
        for director, hostport in six.iteritems(hostports):
            for port in hostport:
                if port['protocol'] == 'FC':
                    fc_ports.append(port)
        target_wwns, init_targ_map = (
            self._build_initiator_target_map(connector, fc_ports))

        LOG.debug('migration_status:%s', volume['migration_status'])
        migstat = volume['migration_status']
        if migstat is not None and 'target:' in migstat:
            index = migstat.find('target:')
            if index != -1:
                migstat = migstat[len('target:'):]
            ldname = (
                self._common.get_ldname(migstat,
                                        self._properties['ld_name_format']))
        else:
            ldname = (
                self._common.get_ldname(volume['id'],
                                        self._properties['ld_name_format']))

        # get lun.
        if ldname not in lds:
            msg = (_('Logical Disk %(ld)s has unbound already. '
                     'volume_id = %(id)s.') %
                   {'ld': ldname, 'id': volume['id']})
            LOG.error(msg)
            raise exception.NotFound(msg)
        ldn = lds[ldname]['ldn']

        lun = None
        for ldset in six.itervalues(ldsets):
            if ldn in ldset['lds']:
                lun = ldset['lds'][ldn]['lun']
                break

        info = {
            'driver_volume_type': 'fibre_channel',
            'data': {'target_lun': lun,
                     'target_wwn': target_wwns,
                     'initiator_target_map': init_targ_map}}

        LOG.debug('_fc_initialize_connection'
                  '(Volume ID = %(id)s, connector = %(connector)s, '
                  'info = %(info)s) End.',
                  {'id': volume['id'],
                   'connector': connector,
                   'info': info})
        return info

    def fc_terminate_connection(self, volume, connector):
        msgparm = ('Volume ID = %(id)s, Connector = %(connector)s'
                   % {'id': volume['id'], 'connector': connector})

        try:
            ret = self._fc_terminate_connection(volume, connector)
            LOG.info('Terminated FC Connection (%s)', msgparm)
            return ret
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Terminate FC Connection '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _fc_terminate_connection(self, volume, connector):
        """Disallow connection from connector."""
        LOG.debug('_fc_terminate_connection'
                  '(Volume ID = %(id)s, connector = %(connector)s) Start.',
                  {'id': volume['id'], 'connector': connector})

        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # get target wwpns and initiator/target map.
        fc_ports = []
        for director, hostport in six.iteritems(hostports):
            for port in hostport:
                if port['protocol'] == 'FC':
                    fc_ports.append(port)
        target_wwns, init_targ_map = (
            self._build_initiator_target_map(connector, fc_ports))

        info = {'driver_volume_type': 'fibre_channel',
                'data': {'target_wwn': target_wwns,
                         'initiator_target_map': init_targ_map}}
        LOG.debug('_fc_terminate_connection'
                  '(Volume ID = %(id)s, connector = %(connector)s, '
                  'info = %(info)s) End.',
                  {'id': volume['id'],
                   'connector': connector,
                   'info': info})
        return info

    def _build_initiator_target_map(self, connector, fc_ports):
        target_wwns = []
        for port in fc_ports:
            target_wwns.append(port['wwpn'])

        initiator_wwns = connector['wwpns']

        init_targ_map = {}
        for initiator in initiator_wwns:
            init_targ_map[initiator] = target_wwns

        return target_wwns, init_targ_map

    def _update_volume_status(self):
        """Retrieve status info from volume group."""

        data = {}

        data['volume_backend_name'] = (self._properties['backend_name'] or
                                       self._driver_name)
        data['vendor_name'] = self._properties['vendor_name']
        data['driver_version'] = self.VERSION
        data['reserved_percentage'] = self._properties['reserved_percentage']
        data['QoS_support'] = True

        # Get xml data from file and parse.
        try:
            pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
                self._common.parse_xml())

            # Get capacities from pools.
            pool_capacity = self._common.get_pool_capacity(pools, ldsets)

            data['total_capacity_gb'] = pool_capacity['total_capacity_gb']
            data['free_capacity_gb'] = pool_capacity['free_capacity_gb']
        except Exception:
            LOG.debug('_update_volume_status Unexpected error. '
                      'exception=%s',
                      traceback.format_exc())
            data['total_capacity_gb'] = 0
            data['free_capacity_gb'] = 0
        return data

    def iscsi_get_volume_stats(self, refresh=False):
        """Get volume status.

        If 'refresh' is True, run update the stats first.
        """
        if refresh:
            self._stats = self._update_volume_status()
            self._stats['storage_protocol'] = 'iSCSI'
        LOG.debug('data=%(data)s, config_group=%(group)s',
                  {'data': self._stats,
                   'group': self._properties['config_group']})

        return self._stats

    def fc_get_volume_stats(self, refresh=False):
        """Get volume status.

        If 'refresh' is True, run update the stats first.
        """

        if refresh:
            self._stats = self._update_volume_status()
            self._stats['storage_protocol'] = 'FC'
        LOG.debug('data=%(data)s, config_group=%(group)s',
                  {'data': self._stats,
                   'group': self._properties['config_group']})

        return self._stats

    def get_pool(self, volume):
        LOG.debug('backend_name=%s', self._properties['backend_name'])
        return self._properties['backend_name']

    def delete_volume(self, volume):
        msgparm = 'Volume ID = %s' % volume['id']
        try:
            self._delete_volume(volume)
            LOG.info('Deleted Volume (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Delete Volume '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _delete_volume(self, volume):
        LOG.debug('_delete_volume Start.')
        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        ldname = (
            self._common.get_ldname(volume['id'],
                                    self._properties['ld_name_format']))
        if ldname not in lds:
            LOG.debug('LD `%s` already unbound?', ldname)
            return

        ld = lds[ldname]

        if ld['RPL Attribute'] == 'IV':
            pass

        elif ld['RPL Attribute'] == 'MV':
            query_status = self._cli.query_MV_RV_status(ldname[3:], 'MV')
            if query_status == 'separated':
                # unpair.
                rvname = self._cli.query_MV_RV_name(ldname[3:], 'MV')
                self._cli.unpair(ldname[3:], rvname, 'force')
            else:
                msg = _('Specified Logical Disk %s has been copied.') % ldname
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        elif ld['RPL Attribute'] == 'RV':
            query_status = self._cli.query_MV_RV_status(ldname[3:], 'RV')
            if query_status == 'separated':
                # unpair.
                mvname = self._cli.query_MV_RV_name(ldname[3:], 'RV')
                self._cli.unpair(mvname, ldname[3:], 'force')
            else:
                msg = _('Specified Logical Disk %s has been copied.') % ldname
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        else:
            msg = (_('RPL Attribute Error. RPL Attribute = %s.')
                   % ld['RPL Attribute'])
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        # Check the LD is remaining on ldset_controller_node.
        ldset_controller_node_name = (
            self._properties['ldset_controller_node_name'])
        if ldset_controller_node_name != '':
            if ldset_controller_node_name in ldsets:
                ldset = ldsets[ldset_controller_node_name]
                if ld['ldn'] in ldset['lds']:
                    LOG.debug('delete LD from ldset_controller_node. '
                              'Ldset Name=%s.',
                              ldset_controller_node_name)
                    self._cli.delldsetld(ldset_controller_node_name, ldname)

        # unbind LD.
        self._cli.unbind(ldname)
        LOG.debug('LD unbound. Name=%s.', ldname)

    def create_snapshot(self, snapshot):
        msgparm = ('Snapshot ID = %(id)s, Snapshot Volume ID = %(vol_id)s'
                   % {'id': snapshot['id'], 'vol_id': snapshot['volume_id']})
        try:
            self._create_snapshot(snapshot)
            LOG.info('Created Snapshot (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create Snapshot '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _create_snapshot(self, snapshot):
        LOG.debug('_create_snapshot'
                  '(Volume ID = %(id)s, Snapshot ID = %(snap_id)s ) Start.',
                  {'id': snapshot['volume_id'], 'snap_id': snapshot['id']})

        if len(self._properties['pool_backup_pools']) == 0:
            LOG.error('backup_pools is not set.')
            raise exception.ParameterNotFound(param='backup_pools')

        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # get MV name.
        ldname = self._common.get_ldname(snapshot['volume_id'],
                                         self._properties['ld_name_format'])

        ld = self._cli.check_ld_existed_rplstatus(lds,
                                                  ldname,
                                                  snapshot,
                                                  'backup')
        ld_capacity = ld['ld_capacity']

        (snapshotname,
         selected_ldn,
         selected_pool) = self._bind_ld(snapshot,
                                        ld_capacity,
                                        None,
                                        self._convert_id2snapname,
                                        self._select_ddr_poolnumber,
                                        ld_capacity)

        volume_properties = {
            'mvname': ldname,
            'rvname': snapshotname,
            'capacity': ld['ld_capacity'],
            'mvid': snapshot['volume_id'],
            'rvid': snapshot['id'],
            'flag': 'backup',
            'context': self._context
        }
        self._cli.backup_restore(volume_properties, cli.UnpairWaitForBackup)

        LOG.debug('_create_snapshot'
                  '(Volume ID = %(vol_id)s, Snapshot ID = %(snap_id)s) End.',
                  {'vol_id': snapshot['volume_id'],
                   'snap_id': snapshot['id']})

    def create_volume_from_snapshot(self, volume, snapshot):
        msgparm = ('Volume ID = %(id)s, '
                   'Snapshot ID = %(snap_id)s, '
                   'Snapshot Volume ID = %(snapvol_id)s'
                   % {'id': volume['id'],
                      'snap_id': snapshot['id'],
                      'snapvol_id': snapshot['volume_id']})
        try:
            self._create_volume_from_snapshot(volume, snapshot)
            LOG.info('Created Volume from Snapshot (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create Volume from Snapshot '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _create_volume_from_snapshot(self, volume, snapshot):
        LOG.debug('_create_volume_from_snapshot'
                  '(Volume ID = %(vol)s, Snapshot ID = %(snap)s) Start.',
                  {'vol': volume['id'], 'snap': snapshot['id']})
        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # get MV name.
        mvname = (
            self._common.get_ldname(snapshot['id'],
                                    self._properties['ld_backupname_format']))
        ld = self._cli.check_ld_existed_rplstatus(lds,
                                                  mvname,
                                                  snapshot,
                                                  'restore')
        mv_capacity = ld['ld_capacity']
        rv_capacity = volume['size']

        (rvname,
         selected_ldn,
         selected_pool) = self._bind_ld(volume,
                                        mv_capacity,
                                        None,
                                        self._convert_id2name,
                                        self._select_volddr_poolnumber,
                                        mv_capacity)

        # check io limit.
        specs = self._common.get_volume_type_qos_specs(volume)
        self._common.check_io_parameter(specs)

        # set io limit.
        self._cli.set_io_limit(rvname, specs)

        if rv_capacity <= mv_capacity:
            rvnumber = None
            rv_capacity = None

        # Restore Start.
        volume_properties = {
            'mvname': mvname,
            'rvname': rvname,
            'capacity': mv_capacity,
            'mvid': snapshot['id'],
            'rvid': volume['id'],
            'rvldn': rvnumber,
            'rvcapacity': rv_capacity,
            'flag': 'restore',
            'context': self._context
        }
        self._cli.backup_restore(volume_properties, cli.UnpairWaitForRestore)

        LOG.debug('_create_volume_from_snapshot'
                  '(Volume ID = %(vol)s, Snapshot ID = %(snap)s, '
                  'Specs=%(specs)s) End.',
                  {'vol': volume['id'],
                   'snap': snapshot['id'],
                   'specs': specs})

    def delete_snapshot(self, snapshot):
        msgparm = ('Snapshot ID = %(id)s, '
                   'Snapshot Volume ID = %(vol_id)s'
                   % {'id': snapshot['id'],
                      'vol_id': snapshot['volume_id']})
        try:
            self._delete_snapshot(snapshot)
            LOG.info('Deleted Snapshot (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Delete Snapshot '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _delete_snapshot(self, snapshot):
        LOG.debug('_delete_snapshot(Snapshot ID = %s) Start.', snapshot['id'])
        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))
        # get ld name.
        ldname = (
            self._common.get_ldname(snapshot['id'],
                                    self._properties['ld_backupname_format']))
        ld = self._cli.check_ld_existed_rplstatus(lds,
                                                  ldname,
                                                  snapshot,
                                                  'delete')
        if ld is None:
            return

        self._cli.unbind(ldname)

        LOG.debug('_delete_snapshot(Snapshot ID = %s) End.', snapshot['id'])


class MStorageDSVDriver(MStorageDriver):
    """M-Series Storage Snapshot helper class."""

    def create_snapshot(self, snapshot):
        msgparm = ('Snapshot ID = %(snap_id)s, '
                   'Snapshot Volume ID = %(snapvol_id)s'
                   % {'snap_id': snapshot['id'],
                      'snapvol_id': snapshot['volume_id']})
        try:
            self._create_snapshot(snapshot,
                                  self._properties['diskarray_name'])
            LOG.info('Created Snapshot (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create Snapshot '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    @coordination.synchronized('mstorage_bind_execute_{diskarray_name}')
    def _create_snapshot(self, snapshot, diskarray_name):
        LOG.debug('_create_snapshot(Volume ID = %(snapvol_id)s, '
                  'Snapshot ID = %(snap_id)s ) Start.',
                  {'snapvol_id': snapshot['volume_id'],
                   'snap_id': snapshot['id']})

        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        if len(self._properties['pool_backup_pools']) == 0:
            LOG.error('backup_pools is not set.')
            raise exception.ParameterNotFound(param='backup_pools')

        # get BV name.
        ldname = self._common.get_ldname(snapshot['volume_id'],
                                         self._properties['ld_name_format'])
        if ldname not in lds:
            msg = _('Logical Disk `%s` has unbound already.') % ldname
            LOG.error(msg)
            raise exception.NotFound(msg)

        selected_pool = self._select_dsv_poolnumber(snapshot, pools, None)
        snapshotname = self._convert_id2snapname(snapshot)
        self._cli.snapshot_create(ldname, snapshotname[3:], selected_pool)

        LOG.debug('_create_snapshot(Volume ID = %(snapvol_id)s, '
                  'Snapshot ID = %(snap_id)s) End.',
                  {'snapvol_id': snapshot['volume_id'],
                   'snap_id': snapshot['id']})

    def delete_snapshot(self, snapshot):
        msgparm = ('Snapshot ID = %(snap_id)s, '
                   'Snapshot Volume ID = %(snapvol_id)s'
                   % {'snap_id': snapshot['id'],
                      'snapvol_id': snapshot['volume_id']})
        try:
            self._delete_snapshot(snapshot)
            LOG.info('Deleted Snapshot (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Delete Snapshot '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _delete_snapshot(self, snapshot):
        LOG.debug('_delete_snapshot(Snapshot ID = %s) Start.',
                  snapshot['id'])
        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # get BV name.
        ldname = self._common.get_ldname(snapshot['volume_id'],
                                         self._properties['ld_name_format'])
        if ldname not in lds:
            LOG.debug('LD(MV) `%s` already unbound?', ldname)
            return

        # get SV name.
        snapshotname = (
            self._common.get_ldname(snapshot['id'],
                                    self._properties['ld_backupname_format']))
        if snapshotname not in lds:
            LOG.debug('LD(SV) `%s` already unbound?', snapshotname)
            return

        self._cli.snapshot_delete(ldname, snapshotname[3:])

        LOG.debug('_delete_snapshot(Snapshot ID = %s) End.', snapshot['id'])

    def create_volume_from_snapshot(self, volume, snapshot):
        msgparm = ('Volume ID = %(vol_id)s, '
                   'Snapshot ID = %(snap_id)s, '
                   'Snapshot Volume ID = %(snapvol_id)s'
                   % {'vol_id': volume['id'],
                      'snap_id': snapshot['id'],
                      'snapvol_id': snapshot['volume_id']})
        try:
            self._create_volume_from_snapshot(volume, snapshot)
            LOG.info('Created Volume from Snapshot (%s)', msgparm)
        except exception.CinderException as e:
            with excutils.save_and_reraise_exception():
                LOG.warning('Failed to Create Volume from Snapshot '
                            '(%(msgparm)s) (%(exception)s)',
                            {'msgparm': msgparm, 'exception': e})

    def _create_volume_from_snapshot(self, volume, snapshot):
        LOG.debug('_create_volume_from_snapshot'
                  '(Volume ID = %(vol_id)s, Snapshot ID(SV) = %(snap_id)s, '
                  'Snapshot ID(BV) = %(snapvol_id)s) Start.',
                  {'vol_id': volume['id'],
                   'snap_id': snapshot['id'],
                   'snapvol_id': snapshot['volume_id']})
        xml = self._cli.view_all(self._properties['ismview_path'])
        pools, lds, ldsets, used_ldns, hostports, max_ld_count = (
            self._common.configs(xml))

        # get BV name.
        mvname = (
            self._common.get_ldname(snapshot['volume_id'],
                                    self._properties['ld_name_format']))

        # get SV name.
        rvname = (
            self._common.get_ldname(snapshot['id'],
                                    self._properties['ld_backupname_format']))

        if rvname not in lds:
            msg = _('Logical Disk `%s` has unbound already.') % rvname
            LOG.error(msg)
            raise exception.NotFound(msg)
        rv = lds[rvname]

        # check snapshot status.
        query_status = self._cli.query_BV_SV_status(mvname[3:], rvname[3:])
        if query_status != 'snap/active':
            msg = (_('Cannot create volume from snapshot, '
                     'because the snapshot data does not exist. '
                     'bvname=%(bvname)s, svname=%(svname)s') %
                   {'bvname': mvname, 'svname': rvname})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        mv_capacity = rv['ld_capacity']
        rv_capacity = volume['size']

        (new_rvname,
         rvnumber,
         selected_pool) = self._bind_ld(volume,
                                        mv_capacity,
                                        None,
                                        self._convert_id2name,
                                        self._select_volddr_poolnumber,
                                        mv_capacity)

        # check io limit.
        specs = self._common.get_volume_type_qos_specs(volume)
        self._common.check_io_parameter(specs)

        # set io limit.
        self._cli.set_io_limit(new_rvname, specs)

        if rv_capacity <= mv_capacity:
            rvnumber = None
            rv_capacity = None

        # Restore Start.
        volume_properties = {
            'mvname': rvname,
            'rvname': new_rvname,
            'prev_mvname': None,
            'capacity': mv_capacity,
            'mvid': snapshot['id'],
            'rvid': volume['id'],
            'rvldn': rvnumber,
            'rvcapacity': rv_capacity,
            'flag': 'esv_restore',
            'context': self._context
        }
        self._cli.backup_restore(volume_properties,
                                 cli.UnpairWaitForDDRRestore)

        LOG.debug('_create_volume_from_snapshot(Volume ID = %(vol_id)s, '
                  'Snapshot ID(SV) = %(snap_id)s, '
                  'Snapshot ID(BV) = %(snapvol_id)s, '
                  'Specs=%(specs)s) End.',
                  {'vol_id': volume['id'],
                   'snap_id': snapshot['id'],
                   'snapvol_id': snapshot['volume_id'],
                   'specs': specs})
