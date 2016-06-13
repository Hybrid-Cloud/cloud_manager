__author__ = 'hgq'

from heat.engine.resources.hwcloud.hws_service.ecs_service import ECSService
from heat.engine.resources.hwcloud.hws_service.evs_service import EVSService
from heat.engine.resources.hwcloud.hws_service.ims_service import IMSService
from heat.engine.resources.hwcloud.hws_service.vpc_service import VPCService
from heat.engine.resources.hwcloud.hws_service.vbs_service import VBSService
import pdb


class HWSClient(object):
    def __init__(self, cloud_info):
        self.ak = cloud_info['access_key']
        self.sk = cloud_info['secret_key']
        self.protocol = cloud_info['protocol']
        self.port = cloud_info['port']
        self.region = cloud_info['region']
        self.host = cloud_info['host']
        self.project_id = cloud_info['project_id']

        host_endpoint = ".".join([self.region, self.host, 'com.cn' ])
        self.ecs_host = 'ecs.' + host_endpoint
        self.evs_host = 'evs.' + host_endpoint
        self.ims_host = 'ims.' + host_endpoint
        self.vpc_host = 'vpc.' + host_endpoint
        self.vbs_host = 'vbs.' + host_endpoint

        self.ecs = ECSService(self.ak, self.sk, self.region, self.protocol, self.ecs_host, self.port)
        self.evs = EVSService(self.ak, self.sk, self.region, self.protocol, self.evs_host, self.port)
        self.ims = IMSService(self.ak, self.sk, self.region, self.protocol, self.ims_host, self.port)
        self.vpc = VPCService(self.ak, self.sk, self.region, self.protocol, self.vpc_host, self.port)
        self.vbs = VBSService(self.ak, self.sk, self.region, self.protocol, self.vbs_host, self.port)

if __name__ == '__main__':
    ak = '5DTFPKOQFEIN4T7EC2BM'
    sk = '00JI0Zeoezqafr03bbWZ7pFc1b4Tw0R7A9oZlFsw'
    region = 'cn-north-1'
    protocol = 'https'
    port = '443'
    hws_client = HWSClient(ak, sk, region, protocol, port)
    project_id = '91d957f0b92d48f0b184c26975d2346e'
    server_id = '72194025-ce73-41a4-a6a4-9637cdf6a0b1'

    image_id = '37ca2b35-6fc7-47ab-93c7-900324809c5c'
    flavor_id = 'c1.medium'
    vpc_id = '742cef84-512c-43fb-a469-8e9e87e35459'
    subnet_id = '7bd9410f-38bb-4fbb-aa7a-cf4a22cb20f3'
    subnet_id_list = [subnet_id]
    root_volume_type = 'SATA'
    availability_zone="cn-north-1a"
    size = 120
    # job_info = hws_client.evs.create_volume(project_id, availability_zone, size, root_volume_type, name='v_1')
    # print job_info

    # job_detail = hws_client.evs.get_job_detail(project_id, '8aace0c8523c082201523f215b0903b3')
    # print job_detail
    volume_id = '9dfd0600-f822-48fa-b831-f43d97135ee5'
    backup_name = 'bk_1'
    server_list = hws_client.ecs.list(project_id)
    print server_list
    # job_info = hws_client.vbs.create_backup(project_id, volume_id, backup_name)
    # print(job_info)
    # job_id = job_info['body']['job_id']
    # job_detail = hws_client.vbs.get_job_detail(project_id, job_id)
