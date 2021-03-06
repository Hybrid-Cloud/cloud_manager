import pdb

import sys
import os


import time
import socket
from heat.openstack.common import log as logging
from heat.engine.resources.cloudmanager.exception import *
from heat.engine.resources.cloudmanager.environmentinfo import *
import vcloud_proxy_install as proxy_installer
import vcloud_cloudinfo as data_handler
import json
import heat.engine.resources.cloudmanager.region_mapping
from heat.engine.resources.cloudmanager.util.subnet_manager import SubnetManager

from heat.engine.resources.cloudmanager.util.retry_decorator import RetryDecorator
from heat.engine.resources.cloudmanager.util.cloud_manager_exception import *

import heat.engine.resources.cloudmanager.util.conf_util as conf_util

from vcloud_cloud_info_persist import *

import heat.engine.resources.cloudmanager.constant as constant

import heat.engine.resources.cloudmanager.proxy_manager as proxy_manager

from pyvcloud import vcloudair
from pyvcloud.vcloudair import VCA
from pyvcloud.schema.vcd.v1_5.schemas.vcloud.networkType import NatRuleType, GatewayNatRuleType, ReferenceType, NatServiceType, FirewallRuleType, ProtocolsType

LOG = logging.getLogger(__name__)

CASCADED_IP_SUFFIX = "4"
CASCADED_IP_FORWARD_SUFFIX = "5"
NTP_IP_SUFFIX = "6"
NTP_IP_STANDBY_SUFFIX = "7"
CPS_WEB_IP = "172.28.11.42"

VPN_IP_SUFFIX = "254"
CASCADED_IP_POOL_MIN = "2"
CASCADED_IP_POOL_MAX = "100"
GATEWAY_IP_SUFFIX = "1"
DHCP_IP_POOL_MIN = "101"
DHCP_IP_POOL_MAX = "200"

NETMASK_24 = "255.255.255.0"
NETMASK_20 = "255.255.240.0"

MAX_RETRY = 100

class VcloudCascadedInstaller:
    def __init__(self, cloud_params=None):
        self.init_params(cloud_params)
        self._read_env()
        self._read_install_info()
        self.installer = vcloudair.VCA(host=self.vcloud_url, username=self.username, service_type='vcd',
                                       version='5.5', verify=False)

    def init_params(self,cloud_params):
        self._read_default_conf()

        if cloud_params == None:
            return
        self.cloud_params = cloud_params

        self.az_name = cloud_params['azname']
        self.access = cloud_params['access']
        self.cloud_type = cloud_params['cloud_type']
        self.driver_type = cloud_params['driver_type']
        self.region = cloud_params['project_info']['RegionName']
        self.vcloud_url = cloud_params['project_info']['VcloudUrl']
        self.vcloud_org = cloud_params['project_info']['VcloudOrg']
        self.vcloud_vdc = cloud_params['project_info']['VcloudVdc']
        self.vcloud_edgegw = cloud_params['project_info']['VcloudEdgegw']
        self.username = cloud_params['project_info']['UserName']
        self.passwd = cloud_params['project_info']['PassWd']
        self.localmode = cloud_params['project_info']['LocalMode']
        self.vcloud_publicip = cloud_params['project_info']['VcloudPublicIP']

        self.cloud_id = "@".join(["VCLOUD", self.cloud_params['azname']])    #get cloud id

        self.install_data_handler = \
            VcloudCloudInfoPersist(constant.VcloudConstant.INSTALL_INFO_FILE, self.cloud_id)
        self.cloud_info_handler = \
            VcloudCloudInfoPersist(constant.VcloudConstant.CLOUD_INFO_FILE, self.cloud_id)

        #ips for NAT
        self.all_public_ip = []
        self.free_public_ip = []

    def _allocate_subnets_cidr(self):
        if self.external_api_cidr is None:
            network = self.cloud_params["network"]
            if network is not None :
                self.internal_base_cidr = network["internal_base_cidr"]
                self.internal_base_name = network["internal_base_name"]
                self.external_api_cidr = network["external_api_cidr"]
                self.external_api_name = network["external_api_name"]
                self.tunnel_bearing_cidr = network["tunnel_bearing_cidr"]
                self.tunnel_bearing_name = network["tunnel_bearing_name"]
            else :
                self.internal_base_cidr = self.default_internal_base_cidr
                self.internal_base_name = self.default_internal_base_name
                self.external_api_name = self.default_external_api_name
                self.tunnel_bearing_name = self.default_tunnel_bearing_name

                subnet_manager = SubnetManager()
                subnet_pair = subnet_manager.distribute_subnet_pair\
                    (self.default_external_api_cidr,  self.default_tunnel_bearing_cidr, constant.VcloudConstant.INSTALL_INFO_FILE)
                self.external_api_cidr = subnet_pair["external_api_cidr"]
                self.tunnel_bearing_cidr = subnet_pair["tunnel_bearing_cidr"]

        self.install_data_handler.write_subnets_cidr(
                                                     self.external_api_cidr,
                                                     self.tunnel_bearing_cidr,
                                                     self.internal_base_cidr,
                                                     self.internal_base_name,
                                                     self.tunnel_bearing_name,
                                                     self.external_api_name
                                                     )


    def _read_env(self):
        try:
            env_info = conf_util.read_conf(constant.VcloudConstant.ENV_FILE)
            self.env = env_info["env"]
            self.cascading_api_ip = env_info["cascading_api_ip"]
            self.cascading_domain = env_info["cascading_domain"]
            self.cascading_vpn_ip = env_info["local_vpn_ip"]
            self.cascading_vpn_public_gw = env_info["local_vpn_public_gw"]
            self.cascading_eip = env_info["cascading_eip"]
            self.cascading_api_subnet = env_info["local_api_subnet"]
            self.cascading_vpn_api_ip = env_info["local_vpn_api_ip"]
            self.cascading_tunnel_subnet = env_info["local_tunnel_subnet"]
            self.cascading_vpn_tunnel_ip = env_info["local_vpn_tunnel_ip"]
            self.existed_cascaded = env_info["existed_cascaded"]
        except ReadEnvironmentInfoFailure as e:
            LOG.error(
                "read environment info error. check the config file: %s"
                % e.message)
            raise ReadEnvironmentInfoFailure(error=e.message)

    def _read_install_info(self):
        #init prarms
        self.cascaded_vm_created = None

        self.public_ip_api_reverse = None
        self.public_ip_api_forward = None
        self.public_ip_ntp_server = None
        self.public_ip_ntp_client = None
        self.public_ip_cps_web = None

        self.cascaded_vpn_vm_created = None
        self.vpn_public_ip = None

        self.proxy_info = None

        self.external_api_existed = None
        self.tunnel_bearing_existed = None
        self.internal_base_existed = None

        self.external_api_cidr = None
        self.tunnel_bearing_cidr = None
        self.internal_base_cidr = None
        self.external_api_name = None
        self.tunnel_bearing_name = None
        self.internal_base_name = None

        install_info = self.install_data_handler.read_cloud_info()
        if not install_info:
            return

        if "cascaded" in install_info.keys():
            cascaded_info = install_info["cascaded"]
            self.cascaded_vm_created = cascaded_info["cascaded_vm_created"]

        if "cascaded_public_ip" in install_info.keys():
            cascaded_public_ip = install_info["cascaded_public_ip"]
            self.public_ip_api_reverse = cascaded_public_ip['public_ip_api_reverse']
            self.public_ip_api_forward = cascaded_public_ip['public_ip_api_forward']
            self.public_ip_ntp_server = cascaded_public_ip['public_ip_ntp_server']
            self.public_ip_ntp_client = cascaded_public_ip['public_ip_ntp_client']
            self.public_ip_cps_web = cascaded_public_ip['public_ip_cps_web']

        if "vpn" in install_info.keys():
            vpn_info = install_info["vpn"]
            self.cascaded_vpn_vm_created = vpn_info["cascaded_vpn_vm_created"]

        if "vpn_public_ip" in install_info.keys():
            vpn_public_ip = install_info["vpn_public_ip"]
            self.vpn_public_ip = vpn_public_ip["vpn_public_ip"]

        if "subnets" in install_info.keys():
            cascaded_subnet_info = install_info["subnets"]
            self.external_api_existed = cascaded_subnet_info["external_api_existed"]
            self.tunnel_bearing_existed = cascaded_subnet_info["tunnel_bearing_existed"]
            self.internal_base_existed = cascaded_subnet_info["internal_base_existed"]

        if "subnets_cidr" in install_info.keys():
            cascaded_subnets_cidr_info = install_info["subnets_cidr"]
            self.external_api_cidr = cascaded_subnets_cidr_info["external_api_cidr"]
            self.tunnel_bearing_cidr = cascaded_subnets_cidr_info["tunnel_bearing_cidr"]
            self.internal_base_cidr = cascaded_subnets_cidr_info["internal_base_cidr"]
            self.external_api_name = cascaded_subnets_cidr_info["external_api_name"]
            self.tunnel_bearing_name = cascaded_subnets_cidr_info["tunnel_bearing_name"]
            self.internal_base_name = cascaded_subnets_cidr_info["internal_base_name"]

        if "proxy_info" in install_info.keys():
            self.proxy_info = install_info["proxy_info"]

    def _read_default_conf(self):
        try:
            self.default_params = conf_util.read_conf(constant.Cascading.VCLOUD_CONF_FILE)

            cascaded_info = self.default_params["cascaded"]
            self.cascaded_image = cascaded_info["cascaded_image"]
            self.catalog_name = cascaded_info['catalog_name']

            vpn_info = self.default_params["vpn"]
            self.vpn_image = vpn_info["vpn_image"]

            network = self.default_params["network"]
            self.default_internal_base_cidr = network["internal_base_cidr"]
            self.default_internal_base_name = network["internal_base_name"]
            self.default_external_api_cidr = network["external_api_cidr"]
            self.default_external_api_name = network["external_api_name"]
            self.default_tunnel_bearing_cidr = network["tunnel_bearing_cidr"]
            self.default_tunnel_bearing_name = network["tunnel_bearing_name"]

        except IOError as e:
            error = "read file = %s error" % constant.VcloudConstant.ENV_FILE
            LOG.error(e)
            raise ReadEnvironmentInfoFailure(error = error)
        except KeyError as e:
            error = "read key = %s error in file = %s" % (e.message, _environment_conf)
            LOG.error(error)
            raise ReadEnvironmentInfoFailure(error = error)

    def create_vm(self, vapp_name, template_name, catalog_name):
        try :
            result = self.installer.create_vapp(self.vcloud_vdc, vapp_name=vapp_name,
                             template_name=template_name,
                             catalog_name=catalog_name,
                             network_name=None,
                             network_mode='bridged',
                             vm_name=None,
                             vm_cpus=None,
                             vm_memory=None,
                             deploy='false',
                             poweron='false')
            if result == False:
                LOG.error('create vm faild vapp=%s. vdc=%s' %(vapp_name,self.vcloud_vdc))
                return False
            else:
                self.installer.block_until_completed(result)
                return True
        except Exception :
            raise InstallCascadedFailed('create vm faild with exception vapp=%s. vdc=%s' %(vapp_name,self.vcloud_vdc))

    def delete_vm(self, vapp_name):
        try :
            result = self.installer.delete_vapp(vdc_name=self.vcloud_vdc,vapp_name=vapp_name)
            if result == False:
                LOG.error('delete vm faild vapp=%s. vdc=%s' %(vapp_name,self.vcloud_vdc))
                return False
            else:
                LOG.info('delete vm success vapp=%s. vdc=%s' %(vapp_name,self.vcloud_vdc))
                self.installer.block_until_completed(result)
                return True
        except Exception :
            LOG.error('delete vm faild with excption vapp=%s. vdc=%s' %(vapp_name,self.vcloud_vdc))
            return False

    def create_network(self):
        pass

    def delete_network(self):
        pass

    def install_proxy(self):
        return proxy_installer.install_vcloud_proxy()

    def _allocate_subnet_ips(self):
        #allocate cascaded ips
        self.internal_base_ip = \
            '.'.join([self.internal_base_cidr.split('.')[0], self.internal_base_cidr.split('.')[1],
                      self.internal_base_cidr.split('.')[2], CASCADED_IP_SUFFIX])
        self.external_api_ip = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], CASCADED_IP_SUFFIX])
        self.tunnel_bearing_ip = \
            '.'.join([self.tunnel_bearing_cidr.split('.')[0], self.tunnel_bearing_cidr.split('.')[1],
                      self.tunnel_bearing_cidr.split('.')[2], CASCADED_IP_SUFFIX])
        self.internal_base_gateway_ip = \
            '.'.join([self.internal_base_cidr.split('.')[0], self.internal_base_cidr.split('.')[1],
                      self.internal_base_cidr.split('.')[2], GATEWAY_IP_SUFFIX])
        self.external_api_gateway_ip = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], GATEWAY_IP_SUFFIX])
        self.tunnel_bearing_gateway_ip = \
            '.'.join([self.tunnel_bearing_cidr.split('.')[0], self.tunnel_bearing_cidr.split('.')[1],
                      self.tunnel_bearing_cidr.split('.')[2], GATEWAY_IP_SUFFIX])
        self.external_api_ip_forward = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], CASCADED_IP_FORWARD_SUFFIX])
        self.ntp_ip_active = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], NTP_IP_SUFFIX])
        self.ntp_ip_standby = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], NTP_IP_STANDBY_SUFFIX])



        #allocate vpn ips
        self.vpn_external_api_ip = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], VPN_IP_SUFFIX])
        self.vpn_tunnel_bearing_ip = \
            '.'.join([self.tunnel_bearing_cidr.split('.')[0], self.tunnel_bearing_cidr.split('.')[1],
                      self.tunnel_bearing_cidr.split('.')[2], VPN_IP_SUFFIX])

        #allocate network ip pool
        self.internal_base_ip_pool_min = \
            '.'.join([self.internal_base_cidr.split('.')[0], self.internal_base_cidr.split('.')[1],
                      self.internal_base_cidr.split('.')[2], CASCADED_IP_POOL_MIN])
        self.external_api_ip_pool_min = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], CASCADED_IP_POOL_MIN])
        self.tunnel_bearing_ip_pool_min = \
            '.'.join([self.tunnel_bearing_cidr.split('.')[0], self.tunnel_bearing_cidr.split('.')[1],
                      self.tunnel_bearing_cidr.split('.')[2], CASCADED_IP_POOL_MIN])
        self.internal_base_ip_pool_max = \
            '.'.join([self.internal_base_cidr.split('.')[0], self.internal_base_cidr.split('.')[1],
                      self.internal_base_cidr.split('.')[2], CASCADED_IP_POOL_MAX])
        self.external_api_ip_pool_max = \
            '.'.join([self.external_api_cidr.split('.')[0], self.external_api_cidr.split('.')[1],
                      self.external_api_cidr.split('.')[2], CASCADED_IP_POOL_MAX])
        self.tunnel_bearing_ip_pool_max = \
            '.'.join([self.tunnel_bearing_cidr.split('.')[0], self.tunnel_bearing_cidr.split('.')[1],
                      self.tunnel_bearing_cidr.split('.')[2], CASCADED_IP_POOL_MAX])

        #allocate dhcp ip pool
        self.internal_base_dhcp_ip_pool_min = \
            '.'.join([self.internal_base_cidr.split('.')[0], self.internal_base_cidr.split('.')[1],
                      self.internal_base_cidr.split('.')[2], DHCP_IP_POOL_MIN])
        self.tunnel_bearing_dhcp_ip_pool_min = \
            '.'.join([self.tunnel_bearing_cidr.split('.')[0], self.tunnel_bearing_cidr.split('.')[1],
                      self.tunnel_bearing_cidr.split('.')[2], DHCP_IP_POOL_MIN])
        self.internal_base_dhcp_ip_pool_max = \
            '.'.join([self.internal_base_cidr.split('.')[0], self.internal_base_cidr.split('.')[1],
                      self.internal_base_cidr.split('.')[2], DHCP_IP_POOL_MAX])
        self.tunnel_bearing_dhcp_ip_pool_max = \
            '.'.join([self.tunnel_bearing_cidr.split('.')[0], self.tunnel_bearing_cidr.split('.')[1],
                      self.tunnel_bearing_cidr.split('.')[2], DHCP_IP_POOL_MAX])

    def cloud_preinstall(self):
        #decide to use default subnet or not
        self._allocate_subnets_cidr()
        #allocate subnet ip
        self._allocate_subnet_ips()


        self.installer_login()
        network_num = []
        #pdb.set_trace()
        if len(network_num) < 3 :
            try :
                self.install_network()
            except InstallCascadingFailed :
                LOG.info("cloud preinstall failed, please check details")
                return

        #
        self.set_free_public_ip()
        self.installer_logout()

    def cloud_install(self):
        self._install_proxy()
        self.install_cascaded()
        if self.localmode != True :
            LOG.info('no need to deploy vpn')
        else :
            self.install_vpn()

    def _install_proxy(self):
        if self.proxy_info is None:
            self.proxy_info = proxy_manager.distribute_proxy()
        self.install_data_handler.write_proxy(self.proxy_info)


    def set_free_public_ip(self):
        if len(self.all_public_ip) == 0 :
            the_gw = self.installer.get_gateway(vdc_name=self.vcloud_vdc,gateway_name=self.vcloud_edgegw)
            self.all_public_ip = sorted(the_gw.get_public_ips(), key=socket.inet_aton)
            all_public_ip_temp = sorted(the_gw.get_public_ips(), key=socket.inet_aton)
            #delete edge ip
            del self.all_public_ip[0]
            del all_public_ip_temp[0]

        #get 10 free public ip from all public ip
        count = 0
        for ip in all_public_ip_temp:
            data = os.system("ping -c 1 %s > /dev/null 2>&1" % ip)
            if data!=0:
                self.free_public_ip.append(ip)
                self.all_public_ip.remove(ip)
                count += 1
            if count > 10:
                break

        if len(self.free_public_ip) == 0:
            LOG.error('set free public ip failed, no free ip can be allocate')


    def get_free_public_ip(self):
        free_ip = self.free_public_ip[0]
        if len(self.free_public_ip) > 0:
            self.free_public_ip.remove(free_ip)
            return free_ip
        else :
            LOG.error('get free public ip failed, no free ip remain')
            return None


    def installer_login(self):
        try :
            self.installer.login(password=self.passwd,org=self.vcloud_org)
            self.installer.login(token=self.installer.token, org=self.vcloud_org,
                                 org_url=self.installer.vcloud_session.org_url)
        except Exception :
            LOG.info("vcloud login failed")

    def installer_logout(self):
        self.installer.logout()

    @RetryDecorator(max_retry_count=100,
                    raise_exception=InstallCascadedFailed(
                        current_step="install network"))
    def install_network(self):
        #pdb.set_trace()
        result = None
        if self.internal_base_existed is None:
            result = self.installer.create_vdc_network(self.vcloud_vdc,network_name=self.internal_base_name,
                                        gateway_name=self.vcloud_edgegw,
                                        start_address=self.internal_base_ip_pool_min,
                                        end_address=self.internal_base_ip_pool_max,
                                        gateway_ip=self.internal_base_gateway_ip,
                                        netmask=NETMASK_20,
                                        dns1=None,
                                        dns2=None,
                                        dns_suffix=None)    #create base net
            if result[0] == False:
                LOG.error('create vcloud base net failed at vdc=%s.' %self.vcloud_vdc)
            else :
                self.installer.block_until_completed(result[1])

        if self.tunnel_bearing_existed is None:
            result = self.installer.create_vdc_network(self.vcloud_vdc,network_name=self.tunnel_bearing_name,
                                        gateway_name=self.vcloud_edgegw,
                                        start_address=self.tunnel_bearing_ip_pool_min,
                                        end_address=self.tunnel_bearing_ip_pool_max,
                                        gateway_ip=self.tunnel_bearing_gateway_ip,
                                        netmask=NETMASK_24,
                                        dns1=None,
                                        dns2=None,
                                        dns_suffix=None)   #create data net
            if result[0] == False:
                LOG.error('create vcloud data net failed at vdc=%s.' %self.vcloud_vdc)
            else :
                self.installer.block_until_completed(result[1])

        if self.external_api_existed is None:
            result = self.installer.create_vdc_network(self.vcloud_vdc,network_name=self.external_api_name,
                                        gateway_name=self.vcloud_edgegw,
                                        start_address=self.external_api_ip_pool_min,
                                        end_address=self.external_api_ip_pool_max,
                                        gateway_ip=self.external_api_gateway_ip,
                                        netmask=NETMASK_24,
                                        dns1=None,
                                        dns2=None,
                                        dns_suffix=None)     #create ext net
            if result[0] == False:
                LOG.error('create vcloud ext net failed at vdc=%s.' %self.vcloud_vdc)
            else :
                self.installer.block_until_completed(result[1])

        network_num = self.installer.get_networks(vdc_name=self.vcloud_vdc)
        if len(network_num) >= 3:
            LOG.info('create vcloud vdc network success.')
            self.internal_base_existed = 'true'
            self.tunnel_bearing_existed = 'true'
            self.external_api_existed = 'true'
            self.install_data_handler.write_subnets_info(
                                           self.internal_base_existed,
                                           self.tunnel_bearing_existed,
                                           self.external_api_existed
                                           )
        else :
            LOG.info('one or more vcloud vdc network create failed. retry more times')
            raise Exception("retry")

    @RetryDecorator(max_retry_count=10,
                    raise_exception=InstallCascadedFailed(
                        current_step="uninstall network"))
    def uninstall_network(self):
        #delete all vdc network
        result = self.installer.delete_vdc_network(self.vcloud_vdc,network_name='base_net')
        if result[0] == False:
            LOG.error('delete vcloud base net failed at vdc=%s.' %self.vcloud_vdc)
        else :
            self.installer.block_until_completed(result[1])

        result = self.installer.delete_vdc_network(self.vcloud_vdc,network_name='data_net')
        if result[0] == False:
            LOG.error('delete vcloud data net failed at vdc=%s.' %self.vcloud_vdc)
        else :
            self.installer.block_until_completed(result[1])

        result = self.installer.delete_vdc_network(self.vcloud_vdc,network_name='ext_net')
        if result[0] == False:
            LOG.error('delete vcloud ext net failed at vdc=%s.' %self.vcloud_vdc)
        else :
            self.installer.block_until_completed(result[1])

        network_num = self.installer.get_networks(vdc_name=self.vcloud_vdc)
        if len(network_num) == 0:
            LOG.info('delete all vcloud vdc network success.')
        else :
            LOG.info('one or more vcloud vdc network delete failed, retry more times.')
            raise Exception("retry")



    def create_vapp_network(self, network_name,vapp_name):
        try :
            the_vdc=self.installer.get_vdc(self.vcloud_vdc)
            the_vapp=self.installer.get_vapp(the_vdc, vapp_name=vapp_name)
            nets = filter(lambda n: n.name == network_name, self.installer.get_networks(self.vcloud_vdc))
            task = the_vapp.connect_to_network(nets[0].name, nets[0].href)
            result = self.installer.block_until_completed(task)

            if result == False:
                LOG.error('create vapp network failed network=%s vapp=%s.' %(network_name, the_vapp.name))
            else :
                LOG.info('create vapp network success network=%s vapp=%s.' %(network_name, the_vapp.name))
        except Exception :
            raise InstallCascadedFailed('create vapp network failed  with excption network=%s vapp=%s.' %(network_name, the_vapp.name))

    def connect_vms_to_vapp_network(self, network_name, vapp_name, nic_index=0, primary_index=0,
                                    mode='DHCP', ipaddr=None):
        try :
            the_vdc=self.installer.get_vdc(self.vcloud_vdc)
            the_vapp=self.installer.get_vapp(the_vdc, vapp_name=vapp_name)
            nets = filter(lambda n: n.name == network_name, self.installer.get_networks(self.vcloud_vdc))
            task = the_vapp.connect_vms(network_name=nets[0].name,
                                    connection_index=nic_index,
                                    connections_primary_index=primary_index,
                                    ip_allocation_mode=mode.upper(),
                                    mac_address=None,
                                    ip_address=ipaddr)
            result = self.installer.block_until_completed(task)

            if result == False:
                LOG.error('connect vms to vapp network failed network=%s vapp=%s.' %(network_name, vapp_name))
            else :
                LOG.info('connect vms to vapp network success network=%s vapp=%s.' %(network_name, vapp_name))
        except Exception :
            raise InstallCascadedFailed('connect vms to vapp network failed with excption network=%s vapp=%s.' %(network_name, vapp_name))


    def add_nat_rule(self, original_ip, translated_ip):
        try :
            the_gw=self.installer.get_gateway(vdc_name=self.vcloud_vdc,gateway_name=self.vcloud_edgegw)
            the_gw.add_nat_rule(rule_type='DNAT',
                   original_ip=original_ip,
                   original_port='any',
                   translated_ip=translated_ip,
                   translated_port='any',
                   protocol='any'
                   )
            the_gw.add_nat_rule(rule_type='SNAT',
                   original_ip=translated_ip,
                   original_port='any',
                   translated_ip=original_ip,
                   translated_port='any',
                   protocol='any'
                   )

            task = the_gw.save_services_configuration()
            result = self.installer.block_until_completed(task)
            if result == False:
                LOG.error('add nat rule failed vdc=%s .' %(self.vcloud_vdc))
            else :
                LOG.info('add nat rule success vdc=%s .' %(self.vcloud_vdc))
        except Exception :
            raise InstallCascadedFailed('add nat rule failed with excption vdc=%s .' %(self.vcloud_vdc))

    def delete_nat_rule(self, original_ip, translated_ip):
        try :
            the_gw=self.installer.get_gateway(vdc_name=self.vcloud_vdc,gateway_name=self.vcloud_edgegw)
            the_gw.del_nat_rule(rule_type='DNAT',
                   original_ip=original_ip,
                   original_port='any',
                   translated_ip=translated_ip,
                   translated_port='any',
                   protocol='any'
                   )
            the_gw.del_nat_rule(rule_type='SNAT',
                   original_ip=translated_ip,
                   original_port='any',
                   translated_ip=original_ip,
                   translated_port='any',
                   protocol='any'
                   )

            task = the_gw.save_services_configuration()
            result = self.installer.block_until_completed(task)
            if result == False:
                LOG.error('delete nat rule failed vdc=%s .' %(self.vcloud_vdc))
            else :
                LOG.info('delete nat rule success vdc=%s .' %(self.vcloud_vdc))
        except Exception :
            LOG.error('delete nat rule failed with excption vdc=%s .' %(self.vcloud_vdc))

    def add_dhcp_pool(self):
        try :
            the_gw=self.installer.get_gateway(vdc_name=self.vcloud_vdc,gateway_name=self.vcloud_edgegw)
            the_gw.add_dhcp_pool(network_name=self.internal_base_name,
                                          low_ip_address=self.internal_base_dhcp_ip_pool_min,
                                          hight_ip_address=self.internal_base_dhcp_ip_pool_max,
                                          default_lease=3600,
                                          max_lease=7200)
            the_gw.add_dhcp_pool(network_name=self.tunnel_bearing_name,
                                          low_ip_address=self.tunnel_bearing_dhcp_ip_pool_min,
                                          hight_ip_address=self.tunnel_bearing_dhcp_ip_pool_max,
                                          default_lease=3600,
                                          max_lease=7200)

            task = the_gw.save_services_configuration()
            result = self.installer.block_until_completed(task)
            if result == False:
                LOG.error('add dhcp failed vdc=%s .' %(self.vcloud_vdc))
            else :
                LOG.info('add dhcp success vdc=%s .' %(self.vcloud_vdc))
        except Exception :
            LOG.error('add dhcp failed with excption vdc=%s .' %(self.vcloud_vdc))

    def delete_dhcp_pool(self):
        try :
            the_gw=self.installer.get_gateway(vdc_name=self.vcloud_vdc,gateway_name=self.vcloud_edgegw)
            the_gw.delete_dhcp_pool(network_name='data_net')
            the_gw.delete_dhcp_pool(network_name='base_net')

            task = the_gw.save_services_configuration()
            result = self.installer.block_until_completed(task)
            if result == False:
                LOG.error('delete dhcp failed vdc=%s .' %(self.vcloud_vdc))
            else :
                LOG.info('delete dhcp success vdc=%s .' %(self.vcloud_vdc))
        except Exception :
            LOG.error('delete dhcp failed with excption vdc=%s .' %(self.vcloud_vdc))

    @RetryDecorator(max_retry_count=MAX_RETRY,
                raise_exception=InstallCascadedFailed(
                    current_step="create vm"))
    def vapp_deploy(self,vapp_name):
        try :
            the_vdc=self.installer.get_vdc(self.vcloud_vdc)
            the_vapp=self.installer.get_vapp(the_vdc, vapp_name=vapp_name)
            task=the_vapp.deploy(powerOn='True')
            result = self.installer.block_until_completed(task)
            if result == False:
                raise InstallCascadedFailed('power on vapp=%s failed vdc=%s .' %(vapp_name,self.vcloud_vdc))
            else :
                LOG.info('power on vapp=%s success vdc=%s .' %(vapp_name,self.vcloud_vdc))
            time.sleep(20)
        except Exception :
            raise InstallCascadedFailed('power on vapp=%s failed with excption vdc=%s .' %(vapp_name,self.vcloud_vdc))

    def vapp_undeploy(self,vapp_name):
        try :
            the_vdc=self.installer.get_vdc(self.vcloud_vdc)
            the_vapp=self.installer.get_vapp(the_vdc, vapp_name=vapp_name)
            task=the_vapp.undeploy(action='powerOff')
            result = self.installer.block_until_completed(task)
            if result == False:
                LOG.error('shutdown  vapp=%s failed vdc=%s .' %(vapp_name,self.vcloud_vdc))
            else :
                LOG.info('shutdown  vapp=%s success vdc=%s .' %(vapp_name,self.vcloud_vdc))
            time.sleep(20)
        except Exception :
            LOG.error('shutdown  vapp=%s failed with excption vdc=%s .' %(vapp_name,self.vcloud_vdc))


    def cloud_postinstall(self):
        pass

    def cloud_preuninstall(self):
        #allocate subnet ip
        self._allocate_subnet_ips()


    def cloud_uninstall(self):
        self.uninstall_cascaded()
        if self.localmode != True :
            LOG.info('no need to delete vpn')
        else :
            self.uninstall_vpn()

    def cloud_postuninstall(self):
        #release distrubute subenet
        subnet_manager = SubnetManager()
        subnet_pair = dict()
        subnet_pair["external_api_cidr"] = self.external_api_cidr
        subnet_manager.release_subnet_pair(subnet_pair, constant.VcloudConstant.INSTALL_INFO_FILE)

        #delete vdc network
        self.installer_login()
        network_num = self.installer.get_networks(vdc_name=self.vcloud_vdc)
        #pdb.set_trace()
        if len(network_num) > 0:
            try :
                self.uninstall_network()
            except InstallCascadingFailed :
                LOG.error("cloud postuninstall failed, please check details")

        self.install_data_handler.delete_cloud_info()
        self.cloud_info_handler.delete_cloud_info()

        self.installer_logout()

    def install_cascaded(self):
         #pdb.set_trace()
         self._install_cascaded()

    def _install_cascaded(self):
        self.installer_login()

        if self.cascaded_vm_created is None:

            result = self.create_vm(vapp_name=self.cascaded_image,
                                             template_name=self.cascaded_image,
                                             catalog_name=self.catalog_name)
            if result == False :
                raise InstallCascadedFailed("create cascaded vm failed.")

            self.cascaded_vm_created = 'true'
            self.install_data_handler.write_cascaded_info(
                                                self.cascaded_vm_created
                                               )

        else :
            LOG.info("the cascaded in this vcloud already existed.")


        self.create_vapp_network(network_name=self.internal_base_name, vapp_name=self.cascaded_image)    #create vapp network
        self.create_vapp_network(network_name=self.external_api_name, vapp_name=self.cascaded_image)
        self.create_vapp_network(network_name=self.tunnel_bearing_name, vapp_name=self.cascaded_image)
        time.sleep(10)

        self.connect_vms_to_vapp_network(network_name=self.internal_base_name, vapp_name=self.cascaded_image, nic_index=0, primary_index=0,
                                                mode='MANUAL', ipaddr=self.internal_base_ip)
        self.connect_vms_to_vapp_network(network_name=self.external_api_name, vapp_name=self.cascaded_image, nic_index=1, primary_index=None,
                                                mode='MANUAL', ipaddr=self.external_api_ip)
        self.connect_vms_to_vapp_network(network_name=self.tunnel_bearing_name, vapp_name=self.cascaded_image, nic_index=2, primary_index=None,
                                                mode='MANUAL', ipaddr=self.tunnel_bearing_ip)
        if self.localmode == True :
            if self.public_ip_api_reverse is None:
                self.public_ip_api_reverse = self.get_free_public_ip()    #add NAT rules to connect ext net
                self.public_ip_api_forward = self.get_free_public_ip()
                self.public_ip_ntp_server = self.get_free_public_ip()
                self.public_ip_ntp_client = self.get_free_public_ip()
                self.public_ip_cps_web = self.get_free_public_ip()
            self.install_data_handler.write_cascaded_public_ip(
                                                          self.public_ip_api_reverse,
                                                          self.public_ip_api_forward,
                                                          self.public_ip_ntp_server,
                                                          self.public_ip_ntp_client,
                                                          self.public_ip_cps_web
                                                          )
            self.add_nat_rule(original_ip=self.public_ip_api_reverse, translated_ip=self.external_api_ip)
            self.add_nat_rule(original_ip=self.public_ip_api_forward, translated_ip=self.external_api_ip_forward)
            self.add_nat_rule(original_ip=self.public_ip_ntp_server, translated_ip=self.ntp_ip_active)
            self.add_nat_rule(original_ip=self.public_ip_ntp_client, translated_ip=self.ntp_ip_standby)
            self.add_nat_rule(original_ip=self.public_ip_cps_web, translated_ip=CPS_WEB_IP)
        else :
            self.public_ip_api_reverse = None    #no need to allocate public ip or add NAT rules
            self.public_ip_api_forward = None
            self.public_ip_ntp_server = None
            self.public_ip_ntp_client = None
            self.public_ip_cps_web = None

        self.add_dhcp_pool()    #add dhcp pool

        self.vapp_deploy(vapp_name=self.cascaded_image)    #poweron the vapp

        self.installer_logout()


        LOG.info("install cascaded success.")

    def uninstall_cascaded(self):
        self._uninstall_cascaded()

    def _uninstall_cascaded(self):
        self.installer_login()

        self.vapp_undeploy(vapp_name=self.cascaded_image)    #power off vapp

        if self.localmode == True :    #delete nat rules and dhcp pool
            self.delete_nat_rule(original_ip=self.public_ip_api_reverse, translated_ip=self.external_api_ip)
            self.delete_nat_rule(original_ip=self.public_ip_api_forward, translated_ip=self.external_api_ip_forward)
            self.delete_nat_rule(original_ip=self.public_ip_ntp_server, translated_ip=self.ntp_ip_active)
            self.delete_nat_rule(original_ip=self.public_ip_ntp_client, translated_ip=self.ntp_ip_standby)
            self.delete_nat_rule(original_ip=self.public_ip_cps_web, translated_ip=CPS_WEB_IP)

        self.delete_dhcp_pool()

        result = self.delete_vm(vapp_name=self.cascaded_image)     #delete vapp
        self.installer_logout()
        if result == False :
            LOG.error("uninstall cascaded failed, please check details.")
        else:
            LOG.info("uninstall cascaded success.")

    def install_vpn(self):
        self._install_vpn()

    def _install_vpn(self):
        self.installer_login()
        if self.cascaded_vpn_vm_created is None:


            result = self.create_vm(vapp_name=self.vpn_image,
                                              template_name=self.vpn_image,
                                              catalog_name=self.catalog_name)

            if result == False :
                raise InstallCascadedFailed("create vpn vm failed.")

            self.cascaded_vpn_vm_created = 'true'
            self.install_data_handler.write_vpn(
                                          self.cascaded_vpn_vm_created
                                    )
        else :
            LOG.info("the vpn in this vcloud already existed.")


        self.create_vapp_network(network_name=self.external_api_name, vapp_name=self.vpn_image)    #create vapp network
        self.create_vapp_network(network_name=self.tunnel_bearing_name, vapp_name=self.vpn_image)
        time.sleep(10)

        self.connect_vms_to_vapp_network(network_name=self.external_api_name, vapp_name=self.vpn_image, nic_index=0, primary_index=0,
                                                 mode='MANUAL', ipaddr=self.vpn_external_api_ip)    #connect vms to vapp network
        self.connect_vms_to_vapp_network(network_name=self.tunnel_bearing_name, vapp_name=self.vpn_image, nic_index=1, primary_index=None,
                                                 mode='MANUAL', ipaddr=self.vpn_tunnel_bearing_ip)

        if self.vpn_public_ip is None:
            self.vpn_public_ip = self.get_free_public_ip()    #add NAT rule to connect ext net
            self.install_data_handler.write_vpn_public_ip(
                                                          self.vpn_public_ip
                                                          )

        self.add_nat_rule(original_ip=self.vpn_public_ip, translated_ip=self.vpn_external_api_ip)


        self.vapp_deploy(vapp_name=self.vpn_image)    #poweron the vapp

        self.installer.logout()

        LOG.info("install vpn success.")



    def uninstall_vpn(self):
        self._uninstall_vpn()

    def _uninstall_vpn(self):
        self.installer_login()


        self.vapp_undeploy(vapp_name=self.vpn_image)    #power off vapp

        self.delete_nat_rule(original_ip=self.vpn_public_ip, translated_ip=self.vpn_external_api_ip)    #delete nat rules

        result = self.delete_vm(vapp_name=self.vpn_image)    #delete vapp
        self.installer_logout()
        if result == False :
            LOG.error("uninstall vpn failed, , please check details.")
        else:
            LOG.info("uninstall vpn success.")


    def package_cloud_info(self):
        return self.package_vcloud_cloud_info()

    def package_vcloud_cloud_info(self):
        cascaded_vpn_info = {
            "public_ip": self.vpn_public_ip,
            "external_api_ip": self.vpn_external_api_ip,
            "tunnel_bearing_ip": self.vpn_tunnel_bearing_ip
        }

        cascaded_info = {
            "public_ip":self.public_ip_api_reverse,
            "external_api_ip": self.external_api_ip,
            "tunnel_bearing_ip": self.tunnel_bearing_ip,
            "internal_base_ip": self.internal_base_ip,
            "domain": self._distribute_cloud_domain(
                     self.region, self.az_name, "--vcloud"),
            "aggregate": self.cascaded_aggregate
        }

        cascaded_subnets_info = {
            "tunnel_bearing_name": self.tunnel_bearing_name,
            "internal_base_name": self.internal_base_name,
            "external_api_name": self.external_api_name,
            "tunnel_bearing": self.tunnel_bearing_cidr,
            "internal_base": self.internal_base_cidr,
            "external_api": self.external_api_cidr,
            "external_api_gateway_ip" : self.external_api_gateway_ip,
        }

        cascading_info = {
            "external_api_ip": self.cascading_api_ip,
            "domain": self.cascading_domain
        }

        cascading_vpn_info = {
            "public_ip": self.cascading_vpn_public_gw,
            "external_api_ip": self.cascading_vpn_api_ip,
            "tunnel_bearing_ip": self.cascading_vpn_tunnel_ip
        }

        cascading_subnets_info = {
            "external_api": self.cascading_api_subnet,
            "tunnel_bearing": self.cascading_tunnel_subnet
        }

        vpn_conn_name = {
            "api_conn_name": self.cloud_id + '-api',
            "tunnel_conn_name": self.cloud_id + '-tunnel'
        }
        #pdb.set_trace()
        info = {"cloud_id": self.cloud_id,
                "access": self.access,
                "cascaded_vpn_info":cascaded_vpn_info,
                "cascading_vpn_info":cascading_vpn_info,
                "cascaded_info": cascaded_info,
                "cascading_info":cascading_info,
                "cascaded_subnets_info": cascaded_subnets_info,
                "cascading_subnets_info": cascading_subnets_info,
                "vpn_conn_name": vpn_conn_name,
                "proxy_info": self.proxy_info
                }

        self.cloud_info_handler.write_cloud_info(info)
        return info


    def _distribute_cloud_domain(self, region_name, azname, az_tag):
        domain_list = self.cascading_domain.split(".")
        domainpostfix = ".".join([domain_list[2], domain_list[3]])
        l_region_name = region_name.lower()
        cloud_cascaded_domain = ".".join(
                [azname, l_region_name + az_tag, domainpostfix])
        self.cascaded_aggregate = ".".join([azname, l_region_name + az_tag])
        return cloud_cascaded_domain

    def get_vcloud_access_cloud_install_info(self,installer):
        return installer.package_vcloud_access_cloud_info()



    def get_cloud_info(self):
        self._read_install_info()
        return self._read_cloud_info()

    def _read_cloud_info(self):
        cloud_info = self.cloud_info_handler.read_cloud_info()
        return cloud_info




