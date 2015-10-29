# -*- coding:utf-8 -*-
__author__ = 'q00222219@huawei'

import threading

from awscloud import AwsCloud
from environmentinfo import *
from commonutils import *


def aws_cloud_2_dict(obj):
    result = {}
    result.update(obj.__dict__)
    return result


def dict_2_aws_cloud(aws_dict):
    if "access" in aws_dict.keys():
        access = aws_dict["access"]
    else:
        access = True

    aws_cloud = AwsCloud(cloud_id=aws_dict["cloud_id"],
                         region=aws_dict["region"], az=aws_dict["az"],
                         access_key_id=aws_dict["access_key_id"],
                         secret_key=aws_dict["secret_key"],
                         cascaded_openstack=aws_dict["cascaded_openstack"],
                         api_vpn=aws_dict["api_vpn"],
                         tunnel_vpn=aws_dict["tunnel_vpn"],
                         vpc_id=aws_dict["vpc_id"],
                         proxy_info=aws_dict["proxy_info"],
                         driver_type=aws_dict["driver_type"],
                         access=access,
                         ceph_vm=aws_dict["ceph_vm"])
    return aws_cloud


aws_cloud_data_file = os.path.join("/home/openstack/cloud_manager",
                                   "data", 'aws_cloud.json')
aws_cloud_data_file_lock = threading.Lock()


class AwsCloudDataHandler(object):
    def __init__(self):
        pass

    def list_aws_clouds(self):
        cloud_dicts = self.__read_aws_cloud_info__()
        return cloud_dicts.keys()

    def get_aws_cloud(self, cloud_id):
        cloud_dicts = self.__read_aws_cloud_info__()
        if cloud_id in cloud_dicts.keys():
            return dict_2_aws_cloud(cloud_dicts[cloud_id])
        raise ReadCloudInfoFailure(reason="no such cloud, cloud_id=%s"
                                          % cloud_id)

    def delete_aws_cloud(self, cloud_id):
        aws_cloud_data_file_lock.acquire()
        try:
            cloud_dicts = self.__read_aws_cloud_info__()
            cloud_dicts.pop(cloud_id)
            self.__write_aws_cloud_info__(cloud_dicts)
        except Exception as e:
            logger.error("delete aws cloud data file error, "
                         "cloud_id: %s, error: %s"
                         % (cloud_id, e.message))
        finally:
            aws_cloud_data_file_lock.release()

    def add_aws_cloud(self, aws_cloud):
        aws_cloud_data_file_lock.acquire()
        try:
            cloud_dicts = self.__read_aws_cloud_info__()
            dict_temp = aws_cloud_2_dict(aws_cloud)
            cloud_dicts[aws_cloud.cloud_id] = dict_temp
            self.__write_aws_cloud_info__(cloud_dicts)
        except Exception as e:
            logger.error("add aws cloud data file error, "
                         "aws_cloud: %s, error: %s"
                         % (aws_cloud, e.message))
        finally:
            aws_cloud_data_file_lock.release()

    @staticmethod
    def __read_aws_cloud_info__():
        if not os.path.exists(aws_cloud_data_file):
            logger.error("read %s : No such file." % aws_cloud_data_file)
            cloud_dicts = {}
        else:
            with open(aws_cloud_data_file, 'r+') as fd:
                cloud_dicts = json.loads(fd.read())
        return cloud_dicts

    @staticmethod
    def __write_aws_cloud_info__(cloud_dicts):
        with open(aws_cloud_data_file, 'w+') as fd:
            fd.write(json.dumps(cloud_dicts, indent=4))