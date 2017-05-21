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
"""Tests for the IBM DS8K family driver."""
import ast
import copy
import eventlet
import json
import mock
import six

from cinder import context
from cinder import exception
from cinder.objects import fields
from cinder import test
from cinder.tests.unit import utils as testutils
from cinder.volume import configuration as conf
import cinder.volume.drivers.ibm.ibm_storage as storage
from cinder.volume.drivers.ibm.ibm_storage import proxy
from cinder.volume import group_types
from cinder.volume import volume_types

# mock decorator logger for all unit test cases.
mock_logger = mock.patch.object(proxy, 'logger', lambda x: x)
mock_logger.start()
from cinder.volume.drivers.ibm.ibm_storage import (
    ds8k_replication as replication)
from cinder.volume.drivers.ibm.ibm_storage import ds8k_helper as helper
from cinder.volume.drivers.ibm.ibm_storage import ds8k_proxy as ds8kproxy
from cinder.volume.drivers.ibm.ibm_storage import ds8k_restclient as restclient
mock_logger.stop()

TEST_VOLUME_ID = '0001'
TEST_HOST_ID = 'H1'
TEST_VOLUME_BACKEND_NAME = 'ds8k_backend'
TEST_GROUP_HOST = 'test_host@' + TEST_VOLUME_BACKEND_NAME + '#fakepool'
TEST_LUN_ID = '00'
TEST_POOLS_STR = 'P0,P1'
TEST_POOL_ID_1 = 'P0'
TEST_POOL_ID_2 = 'P1'
TEST_SOURCE_DS8K_IP = '1.1.1.1'
TEST_TARGET_DS8K_IP = '2.2.2.2'
TEST_SOURCE_WWNN = '5000000000FFC111'
TEST_TARGET_WWNN = '5000000000FFD222'
TEST_SOURCE_WWPN_1 = '10000090FA3418BC'
TEST_SOURCE_WWPN_2 = '10000090FA3418BD'
TEST_SOURCE_IOPORT = 'I0001'
TEST_TARGET_IOPORT = 'I0002'
TEST_LSS_ID_1 = '00'
TEST_LSS_ID_2 = '01'
TEST_LSS_ID_3 = '02'
TEST_PPRC_PATH_ID_1 = (TEST_SOURCE_WWNN + "_" + TEST_LSS_ID_1 + ":" +
                       TEST_TARGET_WWNN + "_" + TEST_LSS_ID_1)
TEST_PPRC_PATH_ID_2 = (TEST_TARGET_WWNN + "_" + TEST_LSS_ID_1 + ":" +
                       TEST_SOURCE_WWNN + "_" + TEST_LSS_ID_1)
TEST_ECKD_VOLUME_ID = '1001'
TEST_ECKD_POOL_ID = 'P10'
TEST_LCU_ID = '10'
TEST_ECKD_PPRC_PATH_ID = (TEST_SOURCE_WWNN + "_" + TEST_LCU_ID + ":" +
                          TEST_TARGET_WWNN + "_" + TEST_LCU_ID)
TEST_SOURCE_SYSTEM_UNIT = u'2107-1111111'
TEST_TARGET_SYSTEM_UNIT = u'2107-2222222'
TEST_SOURCE_VOLUME_ID = TEST_VOLUME_ID
TEST_TARGET_VOLUME_ID = TEST_VOLUME_ID
TEST_PPRC_PAIR_ID = (TEST_SOURCE_SYSTEM_UNIT + '_' +
                     TEST_SOURCE_VOLUME_ID + ':' +
                     TEST_TARGET_SYSTEM_UNIT + '_' +
                     TEST_TARGET_VOLUME_ID)
TEST_FLASHCOPY = {
    'sourcevolume': {'id': 'fake_volume_id_1'},
    'targetvolume': {'id': 'fake_volume_id_2'},
    'persistent': 'enabled',
    'recording': 'enabled',
    'backgroundcopy': 'disabled',
    'state': 'valid'
}
TEST_CONNECTOR = {
    'ip': '192.168.1.2',
    'initiator': 'iqn.1993-08.org.debian:01:fdf9fdfd',
    'wwpns': [TEST_SOURCE_WWPN_1, TEST_SOURCE_WWPN_2],
    'platform': 'x86_64',
    'os_type': 'linux2',
    'host': 'fakehost'
}
TEST_REPLICATION_DEVICE = {
    'san_ip': TEST_TARGET_DS8K_IP,
    'san_login': 'fake',
    'san_clustername': TEST_POOL_ID_1,
    'san_password': 'fake',
    'backend_id': TEST_TARGET_DS8K_IP,
    'connection_type': storage.XIV_CONNECTION_TYPE_FC,
    'ds8k_logical_control_unit_range': ''
}
FAKE_GET_LSS_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "lss":
        [
            {
                "id": TEST_LSS_ID_1,
                "group": "0",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "10"
            },
            {
                "id": TEST_LSS_ID_2,
                "group": "1",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "20"
            },
            {
                "id": TEST_LSS_ID_3,
                "group": "0",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "30"
            },
            {
                "id": "10",
                "group": "0",
                "addrgrp": "1",
                "type": "ckd",
                "configvols": "12"
            }
        ]
    }
}
FAKE_GET_FB_LSS_RESPONSE_1 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "lss":
        [
            {
                "id": TEST_LSS_ID_1,
                "group": "0",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "10",
            }
        ]
    }
}
FAKE_GET_FB_LSS_RESPONSE_2 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "lss":
        [
            {
                "id": TEST_LSS_ID_2,
                "group": "1",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "20",
            }
        ]
    }
}
FAKE_GET_FB_LSS_RESPONSE_3 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "lss":
        [
            {
                "id": TEST_LSS_ID_3,
                "group": "0",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "30",
            }
        ]
    }
}
FAKE_GET_CKD_LSS_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "lss":
        [
            {
                "id": "10",
                "group": "0",
                "addrgrp": "1",
                "type": "ckd",
                "configvols": "10",
            }
        ]
    }
}
FAKE_CREATE_VOLUME_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "volumes":
        [
            {
                "id": TEST_VOLUME_ID,
                "name": "fake_volume"
            }
        ]
    },
    "link":
    {
        "rel": "self",
        "href": "https://1.1.1.1:8452/api/v1/volumes/" + TEST_VOLUME_ID
    }
}
FAKE_GET_PPRC_PATH_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "paths":
        [
            {
                "id": TEST_PPRC_PATH_ID_1,
                "source_lss_id": TEST_LSS_ID_1,
                "target_lss_id": TEST_LSS_ID_1,
                "target_system_wwnn": TEST_TARGET_WWNN,
                "port_pairs":
                [
                    {
                        "source_port_id": TEST_SOURCE_IOPORT,
                        "target_port_id": TEST_TARGET_IOPORT,
                        "state": "success"
                    }
                ]
            },
            {
                "id": TEST_ECKD_PPRC_PATH_ID,
                "source_lss_id": TEST_LCU_ID,
                "target_lss_id": TEST_LCU_ID,
                "target_system_wwnn": TEST_TARGET_WWNN,
                "port_pairs":
                [
                    {
                        "source_port_id": TEST_SOURCE_IOPORT,
                        "target_port_id": TEST_TARGET_IOPORT,
                        "state": "success"
                    }
                ]
            }
        ]
    }
}
FAKE_GET_PPRC_PATH_RESPONSE_1 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "paths":
        [
            {
                "id": TEST_PPRC_PATH_ID_1,
                "source_lss_id": TEST_LSS_ID_1,
                "target_lss_id": TEST_LSS_ID_1,
                "target_system_wwnn": TEST_TARGET_WWNN,
                "port_pairs":
                [
                    {
                        "source_port_id": TEST_SOURCE_IOPORT,
                        "target_port_id": TEST_TARGET_IOPORT,
                        "state": "success"
                    }
                ]
            }
        ]
    }
}
FAKE_GET_PPRC_PATH_RESPONSE_2 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "paths":
        [
            {
                "id": TEST_PPRC_PATH_ID_2,
                "source_lss_id": TEST_LSS_ID_1,
                "target_lss_id": TEST_LSS_ID_1,
                "target_system_wwnn": TEST_SOURCE_WWNN,
                "port_pairs":
                [
                    {
                        "source_port_id": TEST_TARGET_IOPORT,
                        "target_port_id": TEST_SOURCE_IOPORT,
                        "state": "success"
                    }
                ]
            }
        ]
    }
}
FAKE_GET_ECKD_PPRC_PATH_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "paths":
        [
            {
                "id": TEST_ECKD_PPRC_PATH_ID,
                "source_lss_id": TEST_LCU_ID,
                "target_lss_id": TEST_LCU_ID,
                "target_system_wwnn": TEST_TARGET_WWNN,
                "port_pairs":
                [
                    {
                        "source_port_id": TEST_SOURCE_IOPORT,
                        "target_port_id": TEST_TARGET_IOPORT,
                        "state": "success"
                    }
                ]
            }
        ]
    }
}
FAKE_GET_PPRCS_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "pprcs":
        [
            {
                "id": TEST_PPRC_PAIR_ID,
                "source_volume":
                {
                    "name": TEST_SOURCE_VOLUME_ID,
                },
                "source_system":
                {
                    "id": TEST_SOURCE_SYSTEM_UNIT,
                },
                "target_volume":
                {
                    "name": TEST_TARGET_VOLUME_ID,
                },
                "target_system":
                {
                    "id": TEST_TARGET_SYSTEM_UNIT,
                },
                "type": "metro_mirror",
                "state": "full_duplex"
            }
        ]
    }
}
FAKE_GET_POOL_RESPONSE_1 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "pools":
        [
            {
                "id": TEST_POOL_ID_1,
                "name": "P0_OpenStack",
                "node": "0",
                "stgtype": "fb",
                "cap": "10737418240",
                "capavail": "10737418240"
            }
        ]
    }
}
FAKE_GET_POOL_RESPONSE_2 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "pools":
        [
            {
                "id": TEST_POOL_ID_2,
                "name": "P1_OpenStack",
                "node": "1",
                "stgtype": "fb",
                "cap": "10737418240",
                "capavail": "10737418240"
            }
        ]
    }
}
FAKE_GET_ECKD_POOL_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "pools":
        [
            {
                "id": TEST_ECKD_POOL_ID,
                "name": "P10_OpenStack",
                "node": "0",
                "stgtype": "ckd",
                "cap": "10737418240",
                "capavail": "10737418240"
            }
        ]
    }
}
FAKE_GET_TOKEN_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "token":
    {
        "token": "8cf01a2771a04035bcffb7f4a62e9df8",
        "expired_time": "2016-08-06T06:36:54-0700",
        "max_idle_interval": "1800000"
    }
}

FAKE_GET_PHYSICAL_LINKS_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "physical_links":
        [
            {
                "source_port_id": TEST_SOURCE_IOPORT,
                "target_port_id": TEST_TARGET_IOPORT
            }
        ]
    }
}
FAKE_GET_SYSTEM_RESPONSE_1 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "systems":
        [
            {
                "id": TEST_SOURCE_SYSTEM_UNIT,
                "name": "",
                "state": "online",
                "release": "7.5.1",
                "bundle": "87.51.9.0",
                "MTM": "2421-961",
                "sn": "1300741",
                "wwnn": TEST_SOURCE_WWNN,
                "cap": "28019290210304",
                "capalloc": "6933150957568",
                "capavail": "21086139252736",
                "capraw": "40265318400000"
            }
        ]
    }
}
FAKE_GET_SYSTEM_RESPONSE_2 = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "systems":
        [
            {
                "id": TEST_TARGET_SYSTEM_UNIT,
                "name": "",
                "state": "online",
                "release": "7.5.1",
                "bundle": "87.51.9.0",
                "MTM": "2421-962",
                "sn": "1300742",
                "wwnn": TEST_TARGET_WWNN,
                "cap": "20019290210304",
                "capalloc": "4833150957560",
                "capavail": "31086139252736",
                "capraw": "20265318400000"
            }
        ]
    }
}
FAKE_GET_REST_VERSION_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "api_info":
        [
            {
                "bundle_version": "5.7.51.1068"
            }
        ]
    }
}
FAKE_GET_HOST_PORTS_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "host_ports":
        [
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "link": {},
                "state": "logged in",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "lbs": "512",
                "wwnn": "",
                "login_type": "",
                "logical_path_established": "",
                "login_ports": [],
                "host_id": TEST_HOST_ID,
                "host":
                {
                    "name": "OShost:fakehost",
                    "link": {}
                }
            }
        ]
    }
}
FAKE_MAP_VOLUME_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "mappings":
        [
            {
                "lunid": TEST_LUN_ID,
            }
        ]
    },
    "link":
    {
        "rel": "self",
        "href": ("https://1.1.1.1:8452/api/v1/hosts[id=" +
                 TEST_HOST_ID + "]/mappings/" + TEST_LUN_ID)
    }
}
FAKE_GET_IOPORT_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "ioports":
        [
            {
                "id": "I0001",
                "link":
                {
                    "rel": "self",
                    "href": "https://1.1.1.1:8452/api/v1/ioports/I0001"
                },
                "state": "online",
                "protocol": "SCSI-FCP",
                "wwpn": TEST_SOURCE_WWPN_1,
                "type": "Fibre Channel-SW",
                "speed": "8 Gb/s",
                "loc": "U1400.1B3.RJ03177-P1-C1-T0",
                "io_enclosure":
                {
                    "id": "2",
                    "link": {}
                }
            }
        ]
    }
}
FAKE_CREATE_HOST_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "hosts":
        [
            {
                "id": TEST_HOST_ID
            }
        ]
    },
    "link":
    {
        "rel": "self",
        "href": "https://1.1.1.1:8452/api/v1/hosts/testHost_1"
    }
}
FAKE_GET_MAPPINGS_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "mappings":
        [
            {
                "lunid": TEST_LUN_ID,
                "link": {},
                "volume":
                {
                    "id": TEST_VOLUME_ID,
                    "link": {}
                }
            },
            {
                "lunid": "01",
                "link": {},
                "volume":
                {
                    "id": "0002",
                    "link": {}
                }
            }
        ]
    }
}
FAKE_GET_VOLUME_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "data":
    {
        "volumes":
        [
            {
                "id": TEST_VOLUME_ID,
                "link": {},
                "name": "OSvol:vol_1001",
                "pool":
                {
                    "id": TEST_POOL_ID_1,
                    "link": {}
                }
            }
        ]
    }
}
FAKE_GENERIC_RESPONSE = {
    "server":
    {
        "status": "ok",
        "code": "",
        "message": "Operation done successfully."
    },
    "responses":
    [
        {
            "server":
            {
                "status": "ok",
                "code": "",
                "message": "Operation done successfully."
            }
        }
    ]
}
FAKE_DELETE_VOLUME_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_DELETE_PPRC_PAIR_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_FAILBACK_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_FAILOVER_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_CHANGE_VOLUME_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_POST_FLASHCOPIES_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_POST_UNFREEZE_FLASHCOPIES_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_CREATE_LCU_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_ASSIGN_HOST_PORT_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_DELETE_MAPPINGS_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_DELETE_HOST_PORTS_RESPONSE = FAKE_GENERIC_RESPONSE
FAKE_DELETE_HOSTS_RESPONSE = FAKE_GENERIC_RESPONSE

FAKE_REST_API_RESPONSES = {
    TEST_SOURCE_DS8K_IP + '/get':
        FAKE_GET_REST_VERSION_RESPONSE,
    TEST_TARGET_DS8K_IP + '/get':
        FAKE_GET_REST_VERSION_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/systems/get':
        FAKE_GET_SYSTEM_RESPONSE_1,
    TEST_TARGET_DS8K_IP + '/systems/get':
        FAKE_GET_SYSTEM_RESPONSE_2,
    TEST_SOURCE_DS8K_IP + '/volumes/post':
        FAKE_CREATE_VOLUME_RESPONSE,
    TEST_TARGET_DS8K_IP + '/volumes/post':
        FAKE_CREATE_VOLUME_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/volumes/' + TEST_VOLUME_ID + '/get':
        FAKE_GET_VOLUME_RESPONSE,
    TEST_TARGET_DS8K_IP + '/volumes/' + TEST_VOLUME_ID + '/get':
        FAKE_GET_VOLUME_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/volumes/' + TEST_VOLUME_ID + '/put':
        FAKE_CHANGE_VOLUME_RESPONSE,
    TEST_TARGET_DS8K_IP + '/volumes/' + TEST_VOLUME_ID + '/put':
        FAKE_CHANGE_VOLUME_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/volumes/delete':
        FAKE_DELETE_VOLUME_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/volumes/' + TEST_VOLUME_ID + '/delete':
        FAKE_DELETE_VOLUME_RESPONSE,
    TEST_TARGET_DS8K_IP + '/volumes/' + TEST_VOLUME_ID + '/delete':
        FAKE_DELETE_VOLUME_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/lss/get':
        FAKE_GET_LSS_RESPONSE,
    TEST_TARGET_DS8K_IP + '/lss/get':
        FAKE_GET_LSS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/lss/' + TEST_LSS_ID_1 + '/get':
        FAKE_GET_FB_LSS_RESPONSE_1,
    TEST_TARGET_DS8K_IP + '/lss/' + TEST_LSS_ID_1 + '/get':
        FAKE_GET_FB_LSS_RESPONSE_1,
    TEST_SOURCE_DS8K_IP + '/lss/' + TEST_LSS_ID_2 + '/get':
        FAKE_GET_FB_LSS_RESPONSE_2,
    TEST_TARGET_DS8K_IP + '/lss/' + TEST_LSS_ID_2 + '/get':
        FAKE_GET_FB_LSS_RESPONSE_2,
    TEST_SOURCE_DS8K_IP + '/lss/' + TEST_LSS_ID_3 + '/get':
        FAKE_GET_FB_LSS_RESPONSE_3,
    TEST_TARGET_DS8K_IP + '/lss/' + TEST_LSS_ID_3 + '/get':
        FAKE_GET_FB_LSS_RESPONSE_3,
    TEST_SOURCE_DS8K_IP + '/lss/' + TEST_LCU_ID + '/get':
        FAKE_GET_CKD_LSS_RESPONSE,
    TEST_TARGET_DS8K_IP + '/lss/' + TEST_LCU_ID + '/get':
        FAKE_GET_CKD_LSS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/lss/fb/get':
        FAKE_GET_FB_LSS_RESPONSE_1,
    TEST_SOURCE_DS8K_IP + '/lss/ckd/get':
        FAKE_GET_CKD_LSS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/lss/post':
        FAKE_CREATE_LCU_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/pools/' + TEST_POOL_ID_1 + '/get':
        FAKE_GET_POOL_RESPONSE_1,
    TEST_TARGET_DS8K_IP + '/pools/' + TEST_POOL_ID_1 + '/get':
        FAKE_GET_POOL_RESPONSE_1,
    TEST_SOURCE_DS8K_IP + '/pools/' + TEST_POOL_ID_2 + '/get':
        FAKE_GET_POOL_RESPONSE_2,
    TEST_TARGET_DS8K_IP + '/pools/' + TEST_POOL_ID_2 + '/get':
        FAKE_GET_POOL_RESPONSE_2,
    TEST_SOURCE_DS8K_IP + '/pools/' + TEST_ECKD_POOL_ID + '/get':
        FAKE_GET_ECKD_POOL_RESPONSE,
    TEST_TARGET_DS8K_IP + '/pools/' + TEST_ECKD_POOL_ID + '/get':
        FAKE_GET_ECKD_POOL_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/tokens/post':
        FAKE_GET_TOKEN_RESPONSE,
    TEST_TARGET_DS8K_IP + '/tokens/post':
        FAKE_GET_TOKEN_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/paths/' + TEST_PPRC_PATH_ID_1 + '/get':
        FAKE_GET_PPRC_PATH_RESPONSE_1,
    TEST_TARGET_DS8K_IP + '/cs/pprcs/paths/' + TEST_PPRC_PATH_ID_2 + '/get':
        FAKE_GET_PPRC_PATH_RESPONSE_2,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/paths/' + TEST_ECKD_PPRC_PATH_ID + '/get':
        FAKE_GET_ECKD_PPRC_PATH_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/paths/get':
        FAKE_GET_PPRC_PATH_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/get':
        FAKE_GET_PPRCS_RESPONSE,
    TEST_TARGET_DS8K_IP + '/cs/pprcs/get':
        FAKE_GET_PPRCS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/post':
        FAKE_FAILOVER_RESPONSE,
    TEST_TARGET_DS8K_IP + '/cs/pprcs/post':
        FAKE_FAILOVER_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/delete/post':
        FAKE_DELETE_PPRC_PAIR_RESPONSE,
    TEST_TARGET_DS8K_IP + '/cs/pprcs/delete/post':
        FAKE_FAILBACK_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/resume/post':
        FAKE_FAILBACK_RESPONSE,
    TEST_TARGET_DS8K_IP + '/cs/pprcs/resume/post':
        FAKE_FAILBACK_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/flashcopies/post':
        FAKE_POST_FLASHCOPIES_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/flashcopies/unfreeze/post':
        FAKE_POST_UNFREEZE_FLASHCOPIES_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/cs/pprcs/physical_links/get':
        FAKE_GET_PHYSICAL_LINKS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/host_ports/get':
        FAKE_GET_HOST_PORTS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/hosts%5Bid=' + TEST_HOST_ID + '%5D/mappings/post':
        FAKE_MAP_VOLUME_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/ioports/get':
        FAKE_GET_IOPORT_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/hosts/post':
        FAKE_CREATE_HOST_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/host_ports/assign/post':
        FAKE_ASSIGN_HOST_PORT_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/hosts%5Bid=' + TEST_HOST_ID + '%5D/mappings/get':
        FAKE_GET_MAPPINGS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/hosts%5Bid=' + TEST_HOST_ID + '%5D/mappings/' +
    TEST_LUN_ID + '/delete':
        FAKE_DELETE_MAPPINGS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/host_ports/' + TEST_SOURCE_WWPN_2 + '/delete':
        FAKE_DELETE_HOST_PORTS_RESPONSE,
    TEST_SOURCE_DS8K_IP + '/hosts%5Bid=' + TEST_HOST_ID + '%5D/delete':
        FAKE_DELETE_HOSTS_RESPONSE
}


class FakeDefaultRESTConnector(restclient.DefaultRESTConnector):
    """Fake the Default Connector."""

    def connect(self):
        pass

    def send(self, method='', url='', headers=None, payload='', timeout=900):
        host = url.split('https://')[1].split(':8452')[0]
        endpoint = url.split('v1')[1].split('?')[0]
        start = url.index('type') if 'type=' in url else None
        if start:
            type_str = url[start:].split('&')[0].split('=')[1]
        else:
            type_str = ''
        request = host + endpoint + '/' + type_str + method.lower()
        return 200, json.dumps(FAKE_REST_API_RESPONSES[request])


class FakeRESTScheduler(restclient.RESTScheduler):
    """Fake REST Scheduler."""

    def __init__(self, host, user, passw, connector_obj, verify=False):
        self.token = ''
        self.host = host
        self.port = '8452'
        self.user = user
        self.passw = passw
        self.connector = connector_obj or FakeDefaultRESTConnector(verify)
        self.connect()


class FakeDS8KCommonHelper(helper.DS8KCommonHelper):
    """Fake IBM DS8K Helper."""

    def __init__(self, conf, HTTPConnectorObject=None):
        self.conf = conf
        self._connector_obj = HTTPConnectorObject
        self._connection_type = self._get_value('connection_type')
        self._storage_pools = None
        self.backend = {}
        self.setup()

    def _get_value(self, key):
        value = getattr(self.conf, key, None)
        if not value and key not in self.OPTIONAL_PARAMS:
            value = self.conf.get(key)
        return value

    def _create_client(self):
        self._client = FakeRESTScheduler(self._get_value('san_ip'),
                                         self._get_value('san_login'),
                                         self._get_value('san_password'),
                                         None, True)
        self.backend['rest_version'] = self._get_version()['bundle_version']


class FakeDS8KECKDHelper(FakeDS8KCommonHelper, helper.DS8KECKDHelper):
    """Fake IBM DS8K ECKD Helper."""

    pass


class FakeDS8KReplSourceHelper(FakeDS8KCommonHelper,
                               helper.DS8KReplicationSourceHelper):
    """Fake IBM DS8K Replication Target Helper."""

    pass


class FakeDS8KReplTargetHelper(FakeDS8KReplSourceHelper,
                               helper.DS8KReplicationTargetHelper):
    """Fake IBM DS8K Replication Target Helper."""

    pass


class FakeDS8KReplTargetECKDHelper(FakeDS8KECKDHelper,
                                   helper.DS8KReplicationTargetECKDHelper):
    """Fake IBM DS8K Replication Target ECKD Helper."""

    pass


class FakeReplication(replication.Replication):
    """Fake Replication class."""

    def __init__(self, source_helper, device):
        self._source_helper = source_helper
        if device.get('connection_type') == storage.XIV_CONNECTION_TYPE_FC:
            self._target_helper = FakeDS8KReplTargetHelper(device)
        else:
            self._target_helper = FakeDS8KReplTargetECKDHelper(device)
        self._mm_manager = replication.MetroMirrorManager(self._source_helper,
                                                          self._target_helper)


class FakeDS8KProxy(ds8kproxy.DS8KProxy):
    """Fake IBM DS8K Proxy Driver."""

    def __init__(self, storage_info, logger, exception,
                 driver=None, active_backend_id=None,
                 HTTPConnectorObject=None):
        with mock.patch.object(proxy.IBMStorageProxy,
                               '_get_safely_from_configuration') as get_conf:
            get_conf.side_effect = [{}, False]
            proxy.IBMStorageProxy.__init__(self, storage_info, logger,
                                           exception, driver,
                                           active_backend_id)
        self._helper = None
        self._replication = None
        self._connector_obj = HTTPConnectorObject
        self._replication_enabled = False
        self._active_backend_id = active_backend_id
        self.configuration = driver.configuration
        self.consisgroup_cache = {}
        self.setup(None)

    def setup(self, context):
        connection_type = self.configuration.connection_type
        repl_devices = getattr(self.configuration, 'replication_device', None)
        if connection_type == storage.XIV_CONNECTION_TYPE_FC:
            if not repl_devices:
                self._helper = FakeDS8KCommonHelper(self.configuration,
                                                    self._connector_obj)
            else:
                self._helper = FakeDS8KReplSourceHelper(
                    self.configuration, self._connector_obj)
        else:
            self._helper = FakeDS8KECKDHelper(self.configuration,
                                              self._connector_obj)
        # set up replication target
        if repl_devices:
            self._do_replication_setup(repl_devices, self._helper)

    def _do_replication_setup(self, devices, src_helper):
        self._replication = FakeReplication(src_helper, devices[0])
        if self._active_backend_id:
            self._switch_backend_connection(self._active_backend_id)
        else:
            self._replication.check_physical_links()
        self._replication_enabled = True


class DS8KProxyTest(test.TestCase):
    """Test proxy for DS8K volume driver."""

    VERSION = "2.0.0"

    def setUp(self):
        """Initialize IBM DS8K Driver."""
        super(DS8KProxyTest, self).setUp()
        self.ctxt = context.get_admin_context()

        self.configuration = mock.Mock(conf.Configuration)
        self.configuration.connection_type = storage.XIV_CONNECTION_TYPE_FC
        self.configuration.chap = 'disabled'
        self.configuration.san_ip = TEST_SOURCE_DS8K_IP
        self.configuration.management_ips = ''
        self.configuration.san_login = 'fake'
        self.configuration.san_clustername = TEST_POOL_ID_1
        self.configuration.san_password = 'fake'
        self.configuration.volume_backend_name = TEST_VOLUME_BACKEND_NAME
        self.configuration.ds8k_host_type = 'auto'
        self.configuration.reserved_percentage = 0
        self.storage_info = mock.MagicMock()
        self.logger = mock.MagicMock()
        self.exception = mock.MagicMock()

    def _create_volume(self, **kwargs):
        properties = {
            'host': 'openstack@ds8k_backend#ds8k_pool',
            'size': 1
        }
        for p in properties.keys():
            if p not in kwargs:
                kwargs[p] = properties[p]
        return testutils.create_volume(self.ctxt, **kwargs)

    def _create_snapshot(self, **kwargs):
        return testutils.create_snapshot(self.ctxt, **kwargs)

    def _create_group(self, **kwargs):
        return testutils.create_group(self.ctxt, **kwargs)

    def _create_group_snapshot(self, group_id, **kwargs):
        return testutils.create_group_snapshot(self.ctxt,
                                               group_id=group_id,
                                               **kwargs)

    def test_check_host_type(self):
        """host type should be a valid one."""
        self.configuration.ds8k_host_type = 'fake_OS'
        self.assertRaises(exception.InvalidParameterValue,
                          FakeDS8KCommonHelper, self.configuration, None)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_systems')
    def test_verify_version_of_8_0_1(self, mock_get_systems):
        """8.0.1 should not use this driver."""
        mock_get_systems.return_value = {
            "id": TEST_SOURCE_SYSTEM_UNIT,
            "release": "8.0.1",
            "wwnn": TEST_SOURCE_WWNN,
        }
        self.assertRaises(exception.VolumeDriverException,
                          FakeDS8KCommonHelper, self.configuration, None)

    @mock.patch.object(helper.DS8KCommonHelper, '_get_version')
    def test_verify_rest_version_for_5_7_fb(self, mock_get_version):
        """test the min version of REST for fb volume in 7.x."""
        mock_get_version.return_value = {
            "bundle_version": "5.7.50.0"
        }
        self.assertRaises(exception.VolumeDriverException,
                          FakeDS8KCommonHelper, self.configuration, None)

    @mock.patch.object(helper.DS8KCommonHelper, '_get_version')
    def test_verify_rest_version_for_5_8_fb(self, mock_get_version):
        """test the min version of REST for fb volume in 8.1."""
        mock_get_version.return_value = {
            "bundle_version": "5.8.10.0"
        }
        FakeDS8KCommonHelper(self.configuration, None)

    @mock.patch.object(helper.DS8KECKDHelper, '_get_version')
    def test_verify_rest_version_for_5_7_eckd(self, mock_get_version):
        """test the min version of REST for eckd volume in 7.x."""
        self.configuration.connection_type = (
            storage.XIV_CONNECTION_TYPE_FC_ECKD)
        self.configuration.ds8k_devadd_unitadd_mapping = 'C4-10'
        self.configuration.ds8k_ssid_prefix = 'FF'
        self.configuration.san_clustername = TEST_ECKD_POOL_ID
        mock_get_version.return_value = {
            "bundle_version": "5.7.50.0"
        }
        self.assertRaises(exception.VolumeDriverException,
                          FakeDS8KECKDHelper, self.configuration, None)

    @mock.patch.object(helper.DS8KECKDHelper, '_get_version')
    def test_verify_rest_version_for_5_8_eckd_1(self, mock_get_version):
        """test the min version of REST for eckd volume in 8.1."""
        self.configuration.connection_type = (
            storage.XIV_CONNECTION_TYPE_FC_ECKD)
        self.configuration.ds8k_devadd_unitadd_mapping = 'C4-10'
        self.configuration.ds8k_ssid_prefix = 'FF'
        self.configuration.san_clustername = TEST_ECKD_POOL_ID
        mock_get_version.return_value = {
            "bundle_version": "5.8.10.0"
        }
        self.assertRaises(exception.VolumeDriverException,
                          FakeDS8KECKDHelper, self.configuration, None)

    @mock.patch.object(helper.DS8KECKDHelper, '_get_version')
    def test_verify_rest_version_for_5_8_eckd_2(self, mock_get_version):
        """test the min version of REST for eckd volume in 8.2."""
        self.configuration.connection_type = (
            storage.XIV_CONNECTION_TYPE_FC_ECKD)
        self.configuration.ds8k_devadd_unitadd_mapping = 'C4-10'
        self.configuration.ds8k_ssid_prefix = 'FF'
        self.configuration.san_clustername = TEST_ECKD_POOL_ID
        mock_get_version.return_value = {
            "bundle_version": "5.8.20.0"
        }
        self.assertRaises(exception.VolumeDriverException,
                          FakeDS8KECKDHelper, self.configuration, None)

    def test_verify_pools_with_wrong_type(self):
        """pool should be set according to the connection type."""
        self.configuration.san_clustername = TEST_POOLS_STR
        self.configuration.connection_type = (
            storage.XIV_CONNECTION_TYPE_FC_ECKD)
        self.assertRaises(exception.InvalidParameterValue,
                          FakeDS8KCommonHelper, self.configuration, None)

    def test_verify_pools_with_wrong_type_2(self):
        """set wrong connection type should raise exception."""
        self.configuration.connection_type = 'fake_type'
        self.assertRaises(exception.InvalidParameterValue,
                          FakeDS8KCommonHelper, self.configuration, None)

    def test_get_storage_information(self):
        """should get id, wwnn and release fields from system."""
        cmn_helper = FakeDS8KCommonHelper(self.configuration, None)
        self.assertIn('storage_unit', cmn_helper.backend.keys())
        self.assertIn('storage_wwnn', cmn_helper.backend.keys())
        self.assertIn('storage_version', cmn_helper.backend.keys())

    def test_update_stats(self):
        """verify the fields returned by _update_stats."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        expected_result = {
            "volume_backend_name": TEST_VOLUME_BACKEND_NAME,
            "serial_number": TEST_SOURCE_SYSTEM_UNIT,
            "extent_pools": TEST_POOL_ID_1,
            "vendor_name": 'IBM',
            "driver_version": 'IBM Storage (v2.0.0)',
            "storage_protocol": storage.XIV_CONNECTION_TYPE_FC,
            "total_capacity_gb": 10,
            "free_capacity_gb": 10,
            "reserved_percentage": 0,
            "consistent_group_snapshot_enabled": True,
            "multiattach": False
        }

        self.driver._update_stats()
        self.assertDictEqual(expected_result, self.driver.meta['stat'])

    def test_update_stats_when_driver_initialize_failed(self):
        """update stats raises exception if driver initialized failed."""
        with mock.patch(__name__ + '.FakeDS8KCommonHelper') as mock_helper:
            mock_helper.return_value = None
            self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                        self.exception, self)
            self.driver.setup(self.ctxt)
            self.assertRaises(exception.CinderException,
                              self.driver._update_stats)

    def test_update_stats_when_can_not_get_pools(self):
        """update stats raises exception if get pools failed."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        with mock.patch.object(helper.DS8KCommonHelper,
                               'get_pools') as mock_get_pools:
            mock_get_pools.return_value = []
            self.assertRaises(exception.CinderException,
                              self.driver._update_stats)

    def test_find_pool_should_choose_biggest_pool(self):
        """create volume should choose biggest pool."""
        self.configuration.san_clustername = TEST_POOLS_STR
        cmn_helper = FakeDS8KCommonHelper(self.configuration, None)
        pool_id, lss_id = cmn_helper.find_pool_lss_pair(None, False, None)
        self.assertEqual(TEST_POOL_ID_1, pool_id)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_all_lss')
    def test_find_lss_when_lss_in_pprc_path(self, mock_get_all_lss):
        """find LSS when existing LSSs are in PPRC path."""
        mock_get_all_lss.return_value = [{
            "id": TEST_LSS_ID_1,
            "group": "0",
            "addrgrp": "0",
            "type": "fb",
            "configvols": "0"
        }]
        cmn_helper = FakeDS8KCommonHelper(self.configuration, None)
        pool_id, lss_id = cmn_helper.find_pool_lss_pair(None, False, None)
        self.assertNotEqual(TEST_LSS_ID_1, lss_id)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_all_lss')
    def test_find_lss_when_existing_lss_available(self,
                                                  mock_get_all_lss):
        """find LSS when existing LSSs are available."""
        mock_get_all_lss.return_value = [{
            "id": TEST_LSS_ID_2,
            "group": "0",
            "addrgrp": "0",
            "type": "fb",
            "configvols": "0"
        }]
        cmn_helper = FakeDS8KCommonHelper(self.configuration, None)
        pool_id, lss_id = cmn_helper.find_pool_lss_pair(None, False, None)
        self.assertEqual(TEST_LSS_ID_2, lss_id)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_all_lss')
    def test_find_lss_should_choose_emptiest_one(self, mock_get_all_lss):
        """find LSS should choose the emptiest one."""
        mock_get_all_lss.return_value = [
            {
                "id": TEST_LSS_ID_1,
                "group": "0",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "200"
            },
            {
                "id": TEST_LSS_ID_2,
                "group": "0",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "100"
            },
            {
                "id": TEST_LSS_ID_3,
                "group": "0",
                "addrgrp": "0",
                "type": "fb",
                "configvols": "150"
            }
        ]
        cmn_helper = FakeDS8KCommonHelper(self.configuration, None)
        pool_id, lss_id = cmn_helper.find_pool_lss_pair(None, False, None)
        self.assertEqual(TEST_LSS_ID_2, lss_id)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_all_lss')
    @mock.patch.object(helper.DS8KCommonHelper, '_find_from_nonexistent_lss')
    def test_find_lss_when_no_existing_lss_available(self, mock_find_lss,
                                                     mock_get_all_lss):
        """find LSS when no existing LSSs are available."""
        mock_get_all_lss.return_value = [{
            "id": TEST_LSS_ID_2,
            "group": "0",
            "addrgrp": "0",
            "type": "fb",
            "configvols": "256"
        }]
        cmn_helper = FakeDS8KCommonHelper(self.configuration, None)
        pool_id, lss_id = cmn_helper.find_pool_lss_pair(None, False, None)
        self.assertTrue(mock_find_lss.called)

    @mock.patch.object(helper.DS8KCommonHelper, '_find_lss')
    def test_find_lss_when_all_lss_exhausted(self, mock_find_lss):
        """when all LSSs are exhausted should raise exception."""
        cmn_helper = FakeDS8KCommonHelper(self.configuration, None)
        mock_find_lss.return_value = None
        self.assertRaises(restclient.LssIDExhaustError,
                          cmn_helper.find_pool_lss_pair, None, False, None)

    def test_find_lss_for_volume_which_belongs_to_cg(self):
        """find lss for volume, which is in empty CG."""
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        volume = self._create_volume(group_id=group.id)
        lun = ds8kproxy.Lun(volume)
        self.driver._create_lun_helper(lun)
        pid, lss = lun.pool_lss_pair['source']
        self.assertTrue(lss in ['20', '21', '22', '23'])

    def test_find_lss_for_volume_which_belongs_to_cg2(self):
        """find lss for volume, which is in CG having volumes."""
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': '2000'})
        self._create_volume(group_id=group.id,
                            provider_location=location)
        volume = self._create_volume(group_id=group.id)
        lun = ds8kproxy.Lun(volume)
        self.driver._create_lun_helper(lun)
        pid, lss = lun.pool_lss_pair['source']
        self.assertEqual(lss, '20')

    def test_find_lss_for_volume_which_belongs_to_cg3(self):
        """find lss for volume, and other CGs have volumes."""
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        volume = self._create_volume(group_id=group.id)

        group_type2 = group_types.create(
            self.ctxt,
            'group2',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group2 = self._create_group(host=TEST_GROUP_HOST,
                                    group_type_id=group_type2.id)
        location = six.text_type({'vol_hex_id': '2000'})
        self._create_volume(group_id=group2.id,
                            provider_location=location)
        lun = ds8kproxy.Lun(volume)
        self.driver._create_lun_helper(lun)
        pid, lss = lun.pool_lss_pair['source']
        self.assertNotEqual(lss, '20')

    def test_find_lss_for_volume_which_belongs_to_cg4(self):
        """find lss for volume, and other CGs are in error state."""
        self.configuration.lss_range_for_cg = '20'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        volume = self._create_volume(group_id=group.id)

        group_type2 = group_types.create(
            self.ctxt,
            'group2',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group2 = self._create_group(status='error',
                                    host=TEST_GROUP_HOST,
                                    group_type_id=group_type2.id)
        location = six.text_type({'vol_hex_id': '2000'})
        self._create_volume(group_id=group2.id,
                            provider_location=location)
        lun = ds8kproxy.Lun(volume)
        self.driver._create_lun_helper(lun)
        pid, lss = lun.pool_lss_pair['source']
        # error group will be ignored, so LSS 20 can be used.
        self.assertEqual(lss, '20')

    def test_create_volume_and_assign_to_group_with_wrong_host(self):
        # create volume for group which has wrong format of host.
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host="fake_invalid_host",
                                   group_type_id=group_type.id)
        volume = self._create_volume(group_id=group.id)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.create_volume, volume)

    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    def test_create_volume_but_lss_full_afterwards(self, mock_create_lun):
        """create volume in a LSS which is full afterwards."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        volume = self._create_volume(volume_type_id=vol_type.id)
        mock_create_lun.side_effect = [
            restclient.LssFullException('LSS is full.'), TEST_VOLUME_ID]
        vol = self.driver.create_volume(volume)
        self.assertEqual(
            TEST_VOLUME_ID,
            ast.literal_eval(vol['provider_location'])['vol_hex_id'])

    def test_create_volume_of_FB512(self):
        """create volume which type is FB 512."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        volume = self._create_volume(volume_type_id=vol_type.id)
        vol = self.driver.create_volume(volume)
        self.assertEqual('FB 512', vol['metadata']['data_type'])
        self.assertEqual(TEST_VOLUME_ID, vol['metadata']['vol_hex_id'])
        self.assertEqual(
            TEST_VOLUME_ID,
            ast.literal_eval(vol['provider_location'])['vol_hex_id'])

    def test_create_volume_of_OS400_050(self):
        """create volume which type is OS400 050."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        extra_spec = {'drivers:os400': '050'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        volume = self._create_volume(volume_type_id=vol_type.id)
        vol = self.driver.create_volume(volume)
        self.assertEqual(
            TEST_VOLUME_ID,
            ast.literal_eval(vol['provider_location'])['vol_hex_id'])
        self.assertEqual('050 FB 520UV', vol['metadata']['data_type'])

    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    def test_create_eckd_volume(self, mock_create_lun):
        """create volume which type is ECKD."""
        self.configuration.connection_type = (
            storage.XIV_CONNECTION_TYPE_FC_ECKD)
        self.configuration.ds8k_devadd_unitadd_mapping = 'C4-10'
        self.configuration.ds8k_ssid_prefix = 'FF'
        self.configuration.san_clustername = TEST_ECKD_POOL_ID
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        extra_spec = {'drivers:thin_provision': 'False'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        volume = self._create_volume(volume_type_id=vol_type.id)
        mock_create_lun.return_value = TEST_ECKD_VOLUME_ID
        vol = self.driver.create_volume(volume)
        location = ast.literal_eval(vol['provider_location'])
        self.assertEqual('3390', vol['metadata']['data_type'])
        self.assertEqual(TEST_ECKD_VOLUME_ID, vol['metadata']['vol_hex_id'])
        self.assertEqual(TEST_ECKD_VOLUME_ID, location['vol_hex_id'])

    @mock.patch.object(helper.DS8KCommonHelper, 'get_physical_links')
    def test_check_physical_links(self, mock_get_physical_links):
        """check physical links when user do not connect DS8K."""
        src_helper = FakeDS8KCommonHelper(self.configuration, None)
        repl = FakeReplication(src_helper, TEST_REPLICATION_DEVICE)
        mock_get_physical_links.return_value = None
        self.assertRaises(exception.CinderException, repl.check_physical_links)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_physical_links')
    def test_check_physical_links2(self, mock_get_physical_links):
        """check physical links if more than eight physical links."""
        src_helper = FakeDS8KCommonHelper(self.configuration, None)
        repl = FakeReplication(src_helper, TEST_REPLICATION_DEVICE)
        mock_get_physical_links.return_value = [
            {"source_port_id": 'I0001', "target_port_id": 'I0001'},
            {"source_port_id": 'I0002', "target_port_id": 'I0002'},
            {"source_port_id": 'I0003', "target_port_id": 'I0003'},
            {"source_port_id": 'I0004', "target_port_id": 'I0004'},
            {"source_port_id": 'I0005', "target_port_id": 'I0005'},
            {"source_port_id": 'I0006', "target_port_id": 'I0006'},
            {"source_port_id": 'I0007', "target_port_id": 'I0007'},
            {"source_port_id": 'I0008', "target_port_id": 'I0008'},
            {"source_port_id": 'I0009', "target_port_id": 'I0009'}
        ]
        repl.check_physical_links()
        port_pairs = repl._target_helper.backend['port_pairs']
        self.assertEqual(8, len(port_pairs))

    def test_check_physical_links3(self):
        """check physical links when user set them in configure file."""
        src_helper = FakeDS8KCommonHelper(self.configuration, None)
        device = TEST_REPLICATION_DEVICE.copy()
        device['port_pairs'] = TEST_SOURCE_IOPORT + '-' + TEST_TARGET_IOPORT
        repl = FakeReplication(src_helper, device)
        expected_port_pairs = [
            {'source_port_id': TEST_SOURCE_IOPORT,
             'target_port_id': TEST_TARGET_IOPORT}
        ]
        repl.check_physical_links()
        self.assertEqual(expected_port_pairs,
                         repl._target_helper.backend['port_pairs'])

    @mock.patch.object(proxy.IBMStorageProxy, '__init__')
    def test_do_replication_setup(self, mock_init):
        """driver supports only one replication target."""
        replication_device = ['fake_device_1', 'fake_device_2']
        ds8k_proxy = ds8kproxy.DS8KProxy(self.storage_info, self.logger,
                                         self.exception, self)
        self.assertRaises(exception.InvalidParameterValue,
                          ds8k_proxy._do_replication_setup,
                          replication_device, None)

    @mock.patch.object(proxy.IBMStorageProxy, '__init__')
    @mock.patch.object(replication, 'Replication')
    @mock.patch.object(ds8kproxy.DS8KProxy, '_switch_backend_connection')
    def test_switch_backend_connection(self, mock_switch_connection,
                                       mock_replication, mock_proxy_init):
        """driver should switch connection if it has been failed over."""
        ds8k_proxy = ds8kproxy.DS8KProxy(self.storage_info, self.logger,
                                         self.exception, self,
                                         TEST_TARGET_DS8K_IP)
        src_helper = FakeDS8KCommonHelper(self.configuration, None)
        mock_replication.return_value = FakeReplication(
            src_helper, TEST_REPLICATION_DEVICE)
        ds8k_proxy._do_replication_setup(
            [TEST_REPLICATION_DEVICE], src_helper)
        self.assertTrue(mock_switch_connection.called)

    def test_find_lcu_for_eckd_replicated_volume(self):
        """find LCU for eckd replicated volume when pprc path is availble."""
        self.configuration.connection_type = (
            storage.XIV_CONNECTION_TYPE_FC_ECKD)
        self.configuration.ds8k_devadd_unitadd_mapping = 'C4-10'
        self.configuration.ds8k_ssid_prefix = 'FF'
        self.configuration.san_clustername = TEST_ECKD_POOL_ID
        src_helper = FakeDS8KECKDHelper(self.configuration, None)

        device = TEST_REPLICATION_DEVICE.copy()
        device['connection_type'] = storage.XIV_CONNECTION_TYPE_FC_ECKD
        device['ds8k_devadd_unitadd_mapping'] = 'A4-10'
        device['ds8k_ssid_prefix'] = 'FF'
        device['san_clustername'] = TEST_ECKD_POOL_ID
        repl = FakeReplication(src_helper, device)
        repl.check_physical_links()
        pool_lss_pair = repl.find_pool_lss_pair(None)

        expected_pair = {'source': (TEST_ECKD_POOL_ID, TEST_LCU_ID),
                         'target': (TEST_ECKD_POOL_ID, TEST_LCU_ID)}
        self.assertDictEqual(expected_pair, pool_lss_pair)

    @mock.patch.object(eventlet, 'sleep')
    def test_create_fb_replicated_volume(self, mock_sleep):
        """create FB volume when enable replication."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        extra_spec = {'replication_enabled': '<is> True'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        volume = self._create_volume(volume_type_id=vol_type.id)
        vol = self.driver.create_volume(volume)
        self.assertEqual(
            TEST_VOLUME_ID,
            ast.literal_eval(vol['provider_location'])['vol_hex_id'])
        repl = eval(vol['metadata']['replication'])
        self.assertEqual(TEST_VOLUME_ID,
                         repl[TEST_TARGET_DS8K_IP]['vol_hex_id'])

    @mock.patch.object(helper.DS8KCommonHelper, 'get_pprc_paths')
    @mock.patch.object(replication.MetroMirrorManager, 'create_pprc_path')
    @mock.patch.object(eventlet, 'sleep')
    def test_create_fb_replicated_vol_but_no_path_available(self, mock_sleep,
                                                            create_pprc_path,
                                                            get_pprc_paths):
        """create replicated volume but no pprc paths are available."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        extra_spec = {'replication_enabled': '<is> True'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        volume = self._create_volume(volume_type_id=vol_type.id)
        get_pprc_paths.return_value = [
            {
                'source_lss_id': TEST_LSS_ID_1,
                'target_lss_id': TEST_LSS_ID_1,
                'port_pairs': [
                    {
                        'source_port_id': TEST_SOURCE_IOPORT,
                        'target_port_id': TEST_TARGET_IOPORT,
                        'state': 'failed'
                    }
                ],
                'target_system_wwnn': TEST_TARGET_WWNN
            }
        ]
        self.driver.create_volume(volume)
        self.assertTrue(create_pprc_path.called)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_pprc_paths')
    @mock.patch.object(eventlet, 'sleep')
    def test_create_fb_replicated_vol_and_verify_lss_in_path(
            self, mock_sleep, get_pprc_paths):
        """create replicated volume should verify the LSS in pprc paths."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        extra_spec = {'replication_enabled': '<is> True'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        volume = self._create_volume(volume_type_id=vol_type.id)
        get_pprc_paths.return_value = [
            {
                'source_lss_id': TEST_LSS_ID_1,
                'target_lss_id': TEST_LSS_ID_1,
                'port_pairs': [
                    {
                        'source_port_id': TEST_SOURCE_IOPORT,
                        'target_port_id': TEST_TARGET_IOPORT,
                        'state': 'success'
                    }
                ],
                'target_system_wwnn': TEST_TARGET_WWNN
            },
            {
                'source_lss_id': TEST_LSS_ID_2,
                'target_lss_id': TEST_LSS_ID_2,
                'port_pairs': [
                    {
                        'source_port_id': TEST_SOURCE_IOPORT,
                        'target_port_id': TEST_TARGET_IOPORT,
                        'state': 'success'
                    }
                ],
                'target_system_wwnn': TEST_TARGET_WWNN
            }
        ]
        vol = self.driver.create_volume(volume)
        # locate the volume in pprc path which LSS matches the pool.
        self.assertEqual(
            TEST_LSS_ID_1,
            ast.literal_eval(vol['provider_location'])['vol_hex_id'][:2])
        repl = eval(vol['metadata']['replication'])
        self.assertEqual(TEST_LSS_ID_1,
                         repl[TEST_TARGET_DS8K_IP]['vol_hex_id'][:2])

    @mock.patch.object(helper.DS8KCommonHelper, 'get_pprc_paths')
    @mock.patch.object(eventlet, 'sleep')
    def test_create_fb_replicated_vol_when_paths_available(
            self, mock_sleep, get_pprc_paths):
        """create replicated volume when multiple pprc paths are available."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        extra_spec = {'replication_enabled': '<is> True'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        volume = self._create_volume(volume_type_id=vol_type.id)
        get_pprc_paths.return_value = [
            {
                'source_lss_id': TEST_LSS_ID_1,
                'target_lss_id': TEST_LSS_ID_1,
                'port_pairs': [
                    {
                        'source_port_id': TEST_SOURCE_IOPORT,
                        'target_port_id': TEST_TARGET_IOPORT,
                        'state': 'success'
                    }
                ],
                'target_system_wwnn': TEST_TARGET_WWNN
            },
            {
                'source_lss_id': TEST_LSS_ID_3,
                'target_lss_id': TEST_LSS_ID_3,
                'port_pairs': [
                    {
                        'source_port_id': TEST_SOURCE_IOPORT,
                        'target_port_id': TEST_TARGET_IOPORT,
                        'state': 'success'
                    }
                ],
                'target_system_wwnn': TEST_TARGET_WWNN
            }
        ]
        vol = self.driver.create_volume(volume)
        # locate the volume in pprc path which has emptest LSS.
        self.assertEqual(
            TEST_LSS_ID_1,
            ast.literal_eval(vol['provider_location'])['vol_hex_id'][:2])
        repl = eval(vol['metadata']['replication'])
        self.assertEqual(TEST_LSS_ID_1,
                         repl[TEST_TARGET_DS8K_IP]['vol_hex_id'][:2])

    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    @mock.patch.object(eventlet, 'sleep')
    def test_create_replicated_vol_but_lss_full_afterwards(
            self, mock_sleep, create_lun):
        """create replicated volume but lss is full afterwards."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        create_lun.side_effect = [
            restclient.LssFullException('LSS is full.'),
            TEST_VOLUME_ID,
            TEST_VOLUME_ID
        ]
        extra_spec = {'replication_enabled': '<is> True'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        volume = self._create_volume(volume_type_id=vol_type.id)
        with mock.patch.object(replication.MetroMirrorManager,
                               '_is_pprc_paths_healthy') as check_pprc_path:
            check_pprc_path.return_value = replication.PPRC_PATH_HEALTHY
            vol = self.driver.create_volume(volume)
        self.assertEqual(
            TEST_VOLUME_ID,
            ast.literal_eval(vol['provider_location'])['vol_hex_id'])
        repl = eval(vol['metadata']['replication'])
        self.assertEqual(TEST_VOLUME_ID,
                         repl[TEST_TARGET_DS8K_IP]['vol_hex_id'])

    @mock.patch.object(helper.DS8KCommonHelper, '_delete_lun')
    def test_delete_volume(self, mock_delete_lun):
        """delete volume successfully."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)
        self.driver.delete_volume(volume)
        self.assertTrue(mock_delete_lun.called)

    @mock.patch.object(helper.DS8KCommonHelper, '_delete_lun')
    def test_delete_volume_return_if_no_volume_id(self, mock_delete_lun):
        """should not try to delete volume if the volume id is None."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        volume = self._create_volume()
        self.driver.delete_volume(volume)
        self.assertFalse(mock_delete_lun.called)

    @mock.patch.object(helper.DS8KCommonHelper, 'lun_exists')
    @mock.patch.object(helper.DS8KCommonHelper, '_delete_lun')
    def test_delete_volume_return_if_volume_not_exist(self, mock_delete_lun,
                                                      mock_lun_exists):
        """should not delete volume if the volume doesn't exist."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)
        mock_lun_exists.return_value = False
        self.driver.delete_volume(volume)
        self.assertFalse(mock_delete_lun.called)

    @mock.patch.object(helper.DS8KCommonHelper, 'delete_lun_by_id')
    @mock.patch.object(helper.DS8KCommonHelper, 'delete_lun')
    def test_delete_fb_replicated_volume(self, mock_delete_lun,
                                         mock_delete_lun_by_id):
        """Delete volume when enable replication."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        extra_spec = {'replication_enabled': '<is> True'}
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', extra_spec)
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)
        self.driver.delete_volume(volume)
        self.assertTrue(mock_delete_lun_by_id.called)
        self.assertTrue(mock_delete_lun.called)

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_create_cloned_volume(self, mock_get_flashcopy, mock_sleep):
        """clone the volume successfully."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        src_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location)
        location = six.text_type({'vol_hex_id': None})
        tgt_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        volume_update = self.driver.create_cloned_volume(tgt_vol, src_vol)
        self.assertEqual(
            TEST_VOLUME_ID,
            ast.literal_eval(volume_update['provider_location'])['vol_hex_id'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    @mock.patch.object(helper.DS8KCommonHelper, 'change_lun')
    def test_create_cloned_volume2(self, mock_change_lun,
                                   mock_get_flashcopy, mock_sleep):
        """clone from source volume to a bigger target volume."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        src_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location)
        location = six.text_type({'vol_hex_id': None})
        tgt_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location,
                                      size=2)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        self.driver.create_cloned_volume(tgt_vol, src_vol)
        self.assertTrue(mock_change_lun.called)

    def test_create_cloned_volume3(self):
        """clone source volume which should be smaller than target volume."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        src_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location,
                                      size=2)
        location = six.text_type({'vol_hex_id': None})
        tgt_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.create_cloned_volume, tgt_vol, src_vol)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_create_cloned_volume4(self, mock_get_flashcopy):
        """clone a volume which should not be a target in flashcopy."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        src_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location)
        location = six.text_type({'vol_hex_id': None})
        tgt_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location)

        flashcopy_relationship = copy.deepcopy(TEST_FLASHCOPY)
        flashcopy_relationship['targetvolume']['id'] = TEST_VOLUME_ID
        mock_get_flashcopy.return_value = [flashcopy_relationship]
        self.assertRaises(restclient.APIException,
                          self.driver.create_cloned_volume, tgt_vol, src_vol)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    @mock.patch.object(helper.DS8KCommonHelper, 'lun_exists')
    @mock.patch.object(helper.DS8KCommonHelper, 'create_lun')
    def test_create_cloned_volume5(self, mock_create_lun, mock_lun_exists,
                                   mock_get_flashcopy):
        """clone a volume when target has volume ID but it is nonexistent."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        src_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location)
        location = six.text_type({'vol_hex_id': '0003'})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        tgt_vol = self._create_volume(volume_type_id=vol_type.id,
                                      provider_location=location,
                                      volume_metadata=metadata)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        mock_lun_exists.return_value = False
        self.driver.create_cloned_volume(tgt_vol, src_vol)
        self.assertTrue(mock_create_lun.called)

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_create_volume_from_snapshot(self, mock_get_flashcopy, mock_sleep):
        """create volume from snapshot."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        volume = self._create_volume(volume_type_id=vol_type.id)
        location = six.text_type({'vol_hex_id': '0002'})
        snap = self._create_snapshot(volume_id=volume.id,
                                     volume_type_id=vol_type.id,
                                     provider_location=location)
        vol = self._create_volume(volume_type_id=vol_type.id)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        volume_update = self.driver.create_volume_from_snapshot(vol, snap)
        self.assertEqual(
            TEST_VOLUME_ID,
            ast.literal_eval(volume_update['provider_location'])['vol_hex_id'])

    def test_extend_volume(self):
        """extend unreplicated volume."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)
        self.driver.extend_volume(volume, 2)

    @mock.patch.object(eventlet, 'sleep')
    def test_extend_replicated_volume(self, mock_sleep):
        """extend replicated volume."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)
        self.driver.extend_volume(volume, 2)

    def test_extend_replicated_volume_that_has_been_failed_over(self):
        """extend replicated volume which has been failed over should fail."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self, TEST_TARGET_DS8K_IP)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)
        self.assertRaises(exception.CinderException,
                          self.driver.extend_volume, volume, 2)

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_create_snapshot(self, mock_get_flashcopy, mock_sleep):
        """test a successful creation of snapshot."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': '0002'})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)
        snapshot = self._create_snapshot(volume_id=volume.id)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        snapshot_update = self.driver.create_snapshot(snapshot)
        location = ast.literal_eval(snapshot_update['provider_location'])
        self.assertEqual(TEST_VOLUME_ID, location['vol_hex_id'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_retype_from_thick_to_thin(self, mock_get_flashcopy, mock_sleep):
        """retype from thick-provision to thin-provision."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        new_type = {}
        diff = {
            'encryption': {},
            'qos_specs': {},
            'extra_specs': {'drivers:thin_provision': ('False', 'True')}
        }
        host = None
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'drivers:thin_provision': 'False'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        retyped, retype_model_update = self.driver.retype(
            self.ctxt, volume, new_type, diff, host)
        self.assertTrue(retyped)

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_retype_from_thin_to_thick(self, mock_get_flashcopy, mock_sleep):
        """retype from thin-provision to thick-provision."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        new_type = {}
        diff = {
            'encryption': {},
            'qos_specs': {},
            'extra_specs': {'drivers:thin_provision': ('True', 'False')}
        }
        host = None
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'drivers:thin_provision': 'True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        retyped, retype_model_update = self.driver.retype(
            self.ctxt, volume, new_type, diff, host)
        self.assertTrue(retyped)

    @mock.patch.object(eventlet, 'sleep')
    def test_retype_from_unreplicated_to_replicated(self, mock_sleep):
        """retype from unreplicated to replicated."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        new_type = {}
        diff = {
            'encryption': {},
            'qos_specs': {},
            'extra_specs': {
                'replication_enabled': ('<is> False', '<is> True')
            }
        }
        host = None
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> False'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data,
                                     volume_metadata=metadata)

        retyped, retype_model_update = self.driver.retype(
            self.ctxt, volume, new_type, diff, host)
        self.assertTrue(retyped)

    @mock.patch.object(eventlet, 'sleep')
    def test_retype_from_replicated_to_unreplicated(self, mock_sleep):
        """retype from replicated to unreplicated."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        new_type = {}
        diff = {
            'encryption': {},
            'qos_specs': {},
            'extra_specs': {
                'replication_enabled': ('<is> True', '<is> False')
            }
        }
        host = None
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data,
                                     volume_metadata=metadata)

        retyped, retype_model_update = self.driver.retype(
            self.ctxt, volume, new_type, diff, host)
        self.assertTrue(retyped)

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_retype_from_thin_to_thick_and_replicated(self, mock_get_flashcopy,
                                                      mock_sleep):
        """retype from thin-provision to thick-provision and replicated."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        new_type = {}
        diff = {
            'encryption': {},
            'qos_specs': {},
            'extra_specs': {
                'drivers:thin_provision': ('True', 'False'),
                'replication_enabled': ('<is> False', '<is> True')
            }
        }
        host = None
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        retyped, retype_model_update = self.driver.retype(
            self.ctxt, volume, new_type, diff, host)
        self.assertTrue(retyped)

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_retype_from_thin_and_replicated_to_thick(self, mock_get_flashcopy,
                                                      mock_sleep):
        """retype from thin-provision and replicated to thick-provision."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        new_type = {}
        diff = {
            'encryption': {},
            'qos_specs': {},
            'extra_specs': {
                'drivers:thin_provision': ('True', 'False'),
                'replication_enabled': ('<is> True', '<is> False')
            }
        }
        host = None
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        retyped, retype_model_update = self.driver.retype(
            self.ctxt, volume, new_type, diff, host)
        self.assertTrue(retyped)

    def test_retype_replicated_volume_from_thin_to_thick(self):
        """retype replicated volume from thin-provision to thick-provision."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        new_type = {}
        diff = {
            'encryption': {},
            'qos_specs': {},
            'extra_specs': {
                'drivers:thin_provision': ('True', 'False'),
                'replication_enabled': ('<is> True', '<is> True')
            }
        }
        host = None
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)

        self.assertRaises(exception.CinderException, self.driver.retype,
                          self.ctxt, volume, new_type, diff, host)

    def test_migrate_and_try_pools_in_same_rank(self):
        """migrate volume and try pool in same rank."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        self.driver._update_stats()
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        backend = {
            'host': 'host@backend#pool_id',
            'capabilities': {
                'extent_pools': TEST_POOL_ID_1,
                'serial_number': TEST_SOURCE_SYSTEM_UNIT,
                'vendor_name': 'IBM',
                'storage_protocol': 'fibre_channel'
            }
        }
        moved, model_update = self.driver.migrate_volume(
            self.ctxt, volume, backend)
        self.assertTrue(moved)

    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    @mock.patch.object(eventlet, 'sleep')
    def test_migrate_and_try_pools_in_opposite_rank(self, mock_sleep,
                                                    mock_get_flashcopy):
        """migrate volume and try pool in opposite rank."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        self.driver._update_stats()
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        backend = {
            'host': 'host@backend#pool_id',
            'capabilities': {
                'extent_pools': TEST_POOL_ID_2,
                'serial_number': TEST_SOURCE_SYSTEM_UNIT,
                'vendor_name': 'IBM',
                'storage_protocol': 'fibre_channel'
            }
        }
        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        with mock.patch.object(helper.DS8KCommonHelper,
                               '_get_pools') as get_pools:
            get_pools.return_value = FAKE_GET_POOL_RESPONSE_2['data']['pools']
            moved, model_update = self.driver.migrate_volume(
                self.ctxt, volume, backend)
            self.assertTrue(moved)

    def test_initialize_connection_of_fb_volume(self):
        """attach a FB volume to host."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        map_data = self.driver.initialize_connection(volume, TEST_CONNECTOR)
        self.assertEqual(int(TEST_LUN_ID), map_data['data']['target_lun'])

    def test_initialize_connection_of_eckd_volume(self):
        """attach a ECKD volume to host."""
        self.configuration.connection_type = (
            storage.XIV_CONNECTION_TYPE_FC_ECKD)
        self.configuration.ds8k_devadd_unitadd_mapping = 'C4-10'
        self.configuration.ds8k_ssid_prefix = 'FF'
        self.configuration.san_clustername = TEST_ECKD_POOL_ID
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_ECKD_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        map_data = self.driver.initialize_connection(volume, {})
        self.assertEqual(int('C4', 16), map_data['data']['cula'])
        self.assertEqual(int(TEST_ECKD_VOLUME_ID[2:4], 16),
                         map_data['data']['unit_address'])

    @mock.patch.object(helper.DS8KCommonHelper, '_get_host_ports')
    def test_initialize_connection_when_no_existing_host(self,
                                                         mock_get_host_ports):
        """attach volume to host which has not been created."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        host_ports = [
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "state": "unconfigured",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": ''
            }
        ]
        mock_get_host_ports.side_effect = [host_ports]
        map_data = self.driver.initialize_connection(volume, TEST_CONNECTOR)
        self.assertEqual(int(TEST_LUN_ID), map_data['data']['target_lun'])

    @mock.patch.object(helper.DS8KCommonHelper, '_get_host_ports')
    def test_initialize_connection_with_multiple_hosts(self,
                                                       mock_get_host_ports):
        """attach volume to multiple hosts."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        host_ports = [
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "state": "logged in",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": 'H1'
            },
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "state": "logged in",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": 'H2'
            }
        ]
        mock_get_host_ports.side_effect = [host_ports]
        self.assertRaises(restclient.APIException,
                          self.driver.initialize_connection,
                          volume, TEST_CONNECTOR)

    def test_terminate_connection_of_fb_volume(self):
        """detach a FB volume from host."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        self.driver.terminate_connection(volume, TEST_CONNECTOR)

    @mock.patch.object(helper.DS8KCommonHelper, '_get_host_ports')
    def test_terminate_connection_with_multiple_hosts(self,
                                                      mock_get_host_ports):
        """detach volume from multiple hosts."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        host_ports = [
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "state": "logged in",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": 'H1'
            },
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "state": "logged in",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": 'H2'
            }
        ]
        mock_get_host_ports.side_effect = [host_ports]
        self.assertRaises(restclient.APIException,
                          self.driver.terminate_connection,
                          volume, TEST_CONNECTOR)

    @mock.patch.object(helper.DS8KCommonHelper, '_get_host_ports')
    def test_terminate_connection_but_can_not_find_host(self,
                                                        mock_get_host_ports):
        """detach volume but can not find host."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        host_ports = [
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "state": "unconfigured",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": ''
            }
        ]
        mock_get_host_ports.side_effect = [host_ports]
        unmap_data = self.driver.terminate_connection(volume, TEST_CONNECTOR)
        self.assertIsNone(unmap_data)

    @mock.patch.object(helper.DS8KCommonHelper, '_get_host_ports')
    @mock.patch.object(helper.DS8KCommonHelper, '_get_mappings')
    def test_terminate_connection_and_remove_host(self, mock_get_mappings,
                                                  mock_get_host_ports):
        """detach volume and remove host in DS8K."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)

        host_ports = [
            {
                "wwpn": TEST_SOURCE_WWPN_1,
                "state": "logged in",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": TEST_HOST_ID
            },
            {
                "wwpn": TEST_SOURCE_WWPN_2,
                "state": "unconfigured",
                "hosttype": "LinuxRHEL",
                "addrdiscovery": "lunpolling",
                "host_id": ''
            }
        ]
        mappings = [
            {
                "lunid": TEST_LUN_ID,
                "link": {},
                "volume": {"id": TEST_VOLUME_ID, "link": {}}
            }
        ]
        mock_get_host_ports.side_effect = [host_ports]
        mock_get_mappings.side_effect = [mappings]
        self.driver.terminate_connection(volume, TEST_CONNECTOR)

    def test_create_consistency_group(self):
        """user should reserve LSS for consistency group."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.create_group,
                          self.ctxt, group)

    def test_delete_consistency_group_sucessfully(self):
        """test a successful consistency group deletion."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(provider_location=location,
                                     group_id=group.id)
        model_update, volumes_model_update = (
            self.driver.delete_group(self.ctxt, group, [volume]))
        self.assertEqual('deleted', volumes_model_update[0]['status'])
        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update['status'])

    @mock.patch.object(helper.DS8KCommonHelper, 'delete_lun')
    def test_delete_consistency_group_failed(self, mock_delete_lun):
        """test a failed consistency group deletion."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(provider_location=location,
                                     group_id=group.id)
        mock_delete_lun.side_effect = (
            restclient.APIException('delete volume failed.'))
        model_update, volumes_model_update = (
            self.driver.delete_group(self.ctxt, group, [volume]))
        self.assertEqual('error_deleting', volumes_model_update[0]['status'])
        self.assertEqual(fields.GroupStatus.ERROR_DELETING,
                         model_update['status'])

    def test_create_consistency_group_without_reserve_lss(self):
        """user should reserve LSS for group if it enables cg."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.create_group, self.ctxt, group)

    def test_update_generic_group_without_enable_cg(self):
        """update group which not enable cg should return None."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(self.ctxt, 'group', {})
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(provider_location=location)
        model_update, add_volumes_update, remove_volumes_update = (
            self.driver.update_group(self.ctxt, group, [volume], []))
        self.assertIsNone(model_update)
        self.assertIsNone(add_volumes_update)
        self.assertIsNone(remove_volumes_update)

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    @mock.patch.object(helper.DS8KCommonHelper, 'lun_exists')
    def test_update_generic_group_when_enable_cg(self, mock_lun_exists,
                                                 mock_create_lun,
                                                 mock_get_flashcopy,
                                                 mock_sleep):
        """update group, but volume is not in LSS which belongs to group."""
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        volume = self._create_volume(provider_location=location,
                                     volume_metadata=metadata)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        mock_create_lun.return_value = '2200'
        mock_lun_exists.return_value = True
        model_update, add_volumes_update, remove_volumes_update = (
            self.driver.update_group(self.ctxt, group, [volume], []))
        location = ast.literal_eval(add_volumes_update[0]['provider_location'])
        self.assertEqual('2200', location['vol_hex_id'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    @mock.patch.object(helper.DS8KCommonHelper, 'lun_exists')
    def test_update_generic_group_when_enable_cg2(self, mock_lun_exists,
                                                  mock_create_lun,
                                                  mock_get_flashcopy,
                                                  mock_sleep):
        """add replicated volume into group."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)

        vol_type = volume_types.create(
            self.ctxt, 'VOL_TYPE', {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data,
                                     volume_metadata=metadata)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        mock_create_lun.return_value = '2200'
        mock_lun_exists.return_value = True
        model_update, add_volumes_update, remove_volumes_update = (
            self.driver.update_group(self.ctxt, group, [volume], []))
        location = ast.literal_eval(add_volumes_update[0]['provider_location'])
        self.assertEqual('2200', location['vol_hex_id'])

    @mock.patch.object(helper.DS8KCommonHelper, 'delete_lun')
    def test_delete_generic_group_failed(self, mock_delete_lun):
        """test a failed group deletion."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(self.ctxt, 'group', {})
        group = self._create_group(group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(group_type_id=group_type.id,
                                     provider_location=location,
                                     group_id=group.id)
        mock_delete_lun.side_effect = (
            restclient.APIException('delete volume failed.'))
        model_update, volumes_model_update = (
            self.driver.delete_group(self.ctxt, group, [volume]))
        self.assertEqual('error_deleting', volumes_model_update[0]['status'])
        self.assertEqual(fields.GroupStatus.ERROR_DELETING,
                         model_update['status'])

    def test_delete_generic_group_sucessfully(self):
        """test a successful generic group deletion."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(self.ctxt, 'CG', {})
        group = self._create_group(group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(group_type_id=group_type.id,
                                     provider_location=location,
                                     group_id=group.id)
        model_update, volumes_model_update = (
            self.driver.delete_group(self.ctxt, group, [volume]))
        self.assertEqual('deleted', volumes_model_update[0]['status'])
        self.assertEqual(fields.GroupStatus.DELETED, model_update['status'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    def test_create_consistency_group_snapshot_sucessfully(
            self, mock_create_lun, mock_get_flashcopy, mock_sleep):
        """test a successful consistency group snapshot creation."""
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': '2000'})
        volume = self._create_volume(provider_location=location,
                                     group_id=group.id)
        group_snapshot = (
            self._create_group_snapshot(group_id=group.id,
                                        group_type_id=group_type.id))
        snapshot = self._create_snapshot(volume_id=volume.id,
                                         group_snapshot_id=group_snapshot.id)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        mock_create_lun.return_value = '2200'
        model_update, snapshots_model_update = (
            self.driver.create_group_snapshot(
                self.ctxt, group_snapshot, [snapshot]))
        location = ast.literal_eval(
            snapshots_model_update[0]['provider_location'])
        self.assertEqual('2200', location['vol_hex_id'])
        self.assertEqual('available', snapshots_model_update[0]['status'])
        self.assertEqual(fields.GroupStatus.AVAILABLE, model_update['status'])

    def test_delete_consistency_group_snapshot_sucessfully(self):
        """test a successful consistency group snapshot deletion."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)
        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': '2000'})
        volume = self._create_volume(provider_location=location,
                                     group_id=group.id)
        group_snapshot = (
            self._create_group_snapshot(group_id=group.id,
                                        group_type_id=group_type.id))
        snapshot = self._create_snapshot(volume_id=volume.id,
                                         group_snapshot_id=group_snapshot.id)

        model_update, snapshots_model_update = (
            self.driver.delete_group_snapshot(
                self.ctxt, group_snapshot, [snapshot]))
        self.assertEqual('deleted', snapshots_model_update[0]['status'])
        self.assertEqual(fields.GroupSnapshotStatus.DELETED,
                         model_update['status'])

    @mock.patch.object(helper.DS8KCommonHelper, 'delete_lun')
    def test_delete_consistency_group_snapshot_failed(self,
                                                      mock_delete_lun):
        """test a failed consistency group snapshot deletion."""
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        group = self._create_group(group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': '2000'})
        volume = self._create_volume(provider_location=location,
                                     group_id=group.id)
        group_snapshot = (
            self._create_group_snapshot(group_id=group.id,
                                        group_type_id=group_type.id))
        snapshot = self._create_snapshot(volume_id=volume.id,
                                         group_snapshot_id=group_snapshot.id)

        mock_delete_lun.side_effect = (
            restclient.APIException('delete snapshot failed.'))
        model_update, snapshots_model_update = (
            self.driver.delete_group_snapshot(
                self.ctxt, group_snapshot, [snapshot]))
        self.assertEqual('error_deleting', snapshots_model_update[0]['status'])
        self.assertEqual(fields.GroupSnapshotStatus.ERROR_DELETING,
                         model_update['status'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_create_consisgroup_from_consisgroup(self, mock_get_flashcopy,
                                                 mock_create_lun, mock_sleep):
        """test creation of consistency group from consistency group."""
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        src_group = self._create_group(host=TEST_GROUP_HOST,
                                       group_type_id=group_type.id)
        location = six.text_type({'vol_hex_id': '2000'})
        src_vol = self._create_volume(provider_location=location,
                                      group_id=src_group.id)
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        volume = self._create_volume(group_id=group.id)
        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        mock_create_lun.return_value = '2200'
        model_update, volumes_model_update = (
            self.driver.create_group_from_src(
                self.ctxt, group, [volume], None, None, src_group, [src_vol]))
        self.assertEqual('2200',
                         volumes_model_update[0]['metadata']['vol_hex_id'])
        self.assertEqual(fields.GroupStatus.AVAILABLE,
                         model_update['status'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, '_create_lun')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_flashcopy')
    def test_create_consisgroup_from_cgsnapshot(self, mock_get_flashcopy,
                                                mock_create_lun, mock_sleep):
        """test creation of consistency group from cgsnapshot."""
        self.configuration.lss_range_for_cg = '20-23'
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        group_type = group_types.create(
            self.ctxt,
            'group',
            {'consistent_group_snapshot_enabled': '<is> True'}
        )
        src_group = self._create_group(host=TEST_GROUP_HOST,
                                       group_type_id=group_type.id)
        src_vol = self._create_volume(group_id=src_group.id)
        group_snapshot = (
            self._create_group_snapshot(group_id=src_group.id,
                                        group_type_id=group_type.id))
        location = six.text_type({'vol_hex_id': '2000'})
        snapshot = self._create_snapshot(volume_id=src_vol.id,
                                         provider_location=location,
                                         group_snapshot_id=group_snapshot.id)
        group = self._create_group(host=TEST_GROUP_HOST,
                                   group_type_id=group_type.id)
        volume = self._create_volume(group_id=group.id)

        mock_get_flashcopy.side_effect = [[TEST_FLASHCOPY], {}]
        mock_create_lun.return_value = '2200'
        model_update, volumes_model_update = (
            self.driver.create_group_from_src(
                self.ctxt, group, [volume], group_snapshot,
                [snapshot], None, None))
        self.assertEqual(
            '2200', volumes_model_update[0]['metadata']['vol_hex_id'])
        self.assertEqual(fields.GroupStatus.AVAILABLE,
                         model_update['status'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_pprc_pairs')
    def test_failover_host_successfully(self, mock_get_pprc_pairs, mock_sleep):
        """Failover host to valid secondary successfully."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data,
                                     volume_metadata=metadata)
        pprc_pairs = copy.deepcopy(FAKE_GET_PPRCS_RESPONSE['data']['pprcs'])
        pprc_pairs[0]['state'] = 'suspended'
        mock_get_pprc_pairs.side_effect = [pprc_pairs]
        secondary_id, volume_update_list = self.driver.failover_host(
            self.ctxt, [volume], TEST_TARGET_DS8K_IP)
        self.assertEqual(TEST_TARGET_DS8K_IP, secondary_id)

    @mock.patch.object(replication.Replication, 'do_pprc_failover')
    def test_failover_host_failed(self, mock_do_pprc_failover):
        """Failover host should raise exception when failed."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data,
                                     volume_metadata=metadata)

        mock_do_pprc_failover.side_effect = (
            restclient.APIException('failed to do failover.'))
        self.assertRaises(exception.UnableToFailOver,
                          self.driver.failover_host, self.ctxt,
                          [volume], TEST_TARGET_DS8K_IP)

    def test_failover_host_to_invalid_target(self):
        """Failover host to invalid secondary should fail."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)
        self.assertRaises(exception.InvalidReplicationTarget,
                          self.driver.failover_host, self.ctxt,
                          [volume], 'fake_target')

    def test_failover_host_that_has_been_failed_over(self):
        """Failover host that has been failed over should just return."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self, TEST_TARGET_DS8K_IP)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)
        secondary_id, volume_update_list = self.driver.failover_host(
            self.ctxt, [volume], TEST_TARGET_DS8K_IP)
        self.assertEqual(TEST_TARGET_DS8K_IP, secondary_id)
        self.assertEqual([], volume_update_list)

    def test_failback_host_that_has_been_failed_back(self):
        """Failback host that has been failed back should just return."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)
        secondary_id, volume_update_list = self.driver.failover_host(
            self.ctxt, [volume], 'default')
        self.assertIsNone(secondary_id)
        self.assertEqual([], volume_update_list)

    def test_failover_host_which_only_has_unreplicated_volume(self):
        """Failover host which only has unreplicated volume."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)
        secondary_id, volume_update_list = self.driver.failover_host(
            self.ctxt, [volume], TEST_TARGET_DS8K_IP)
        self.assertEqual(TEST_TARGET_DS8K_IP, secondary_id)
        self.assertEqual('error', volume_update_list[0]['updates']['status'])

    def test_failback_should_recover_status_of_unreplicated_volume(self):
        """Failback host should recover the status of unreplicated volume."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self, TEST_TARGET_DS8K_IP)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE', {})
        location = six.text_type({
            'vol_hex_id': TEST_VOLUME_ID,
            'old_status': 'available'
        })
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location)
        secondary_id, volume_update_list = self.driver.failover_host(
            self.ctxt, [volume], 'default')
        self.assertEqual('default', secondary_id)
        self.assertEqual('available',
                         volume_update_list[0]['updates']['status'])

    @mock.patch.object(eventlet, 'sleep')
    @mock.patch.object(helper.DS8KCommonHelper, 'get_pprc_pairs')
    def test_failback_host_successfully(self, mock_get_pprc_pairs, mock_sleep):
        """Failback host to primary successfully."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self, TEST_TARGET_DS8K_IP)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        metadata = [{'key': 'data_type', 'value': 'FB 512'}]
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data,
                                     volume_metadata=metadata)
        pprc_pairs_full_duplex = FAKE_GET_PPRCS_RESPONSE['data']['pprcs']
        pprc_pairs_suspended = copy.deepcopy(pprc_pairs_full_duplex)
        pprc_pairs_suspended[0]['state'] = 'suspended'
        mock_get_pprc_pairs.side_effect = [pprc_pairs_full_duplex,
                                           pprc_pairs_suspended,
                                           pprc_pairs_full_duplex]
        secondary_id, volume_update_list = self.driver.failover_host(
            self.ctxt, [volume], 'default')
        self.assertEqual('default', secondary_id)

    @mock.patch.object(replication.Replication, 'start_pprc_failback')
    def test_failback_host_failed(self, mock_start_pprc_failback):
        """Failback host should raise exception when failed."""
        self.configuration.replication_device = [TEST_REPLICATION_DEVICE]
        self.driver = FakeDS8KProxy(self.storage_info, self.logger,
                                    self.exception, self, TEST_TARGET_DS8K_IP)
        self.driver.setup(self.ctxt)

        vol_type = volume_types.create(self.ctxt, 'VOL_TYPE',
                                       {'replication_enabled': '<is> True'})
        location = six.text_type({'vol_hex_id': TEST_VOLUME_ID})
        data = json.dumps(
            {TEST_TARGET_DS8K_IP: {'vol_hex_id': TEST_VOLUME_ID}})
        volume = self._create_volume(volume_type_id=vol_type.id,
                                     provider_location=location,
                                     replication_driver_data=data)
        mock_start_pprc_failback.side_effect = (
            restclient.APIException('failed to do failback.'))
        self.assertRaises(exception.UnableToFailOver,
                          self.driver.failover_host, self.ctxt,
                          [volume], 'default')
