#!/usr/bin/env bash

dir=`cd "$(dirname "$0")"; pwd`
dir=${dir}/enable_openstack_service

rm -rf ${dir}
mkdir -p ${dir}

RUN_SCRIPT=${dir}/enable_openstack_service_run.sh
RUN_LOG=${dir}/enable_openstack_service_run.log

az_domain=${1}

ifs=$IFS
IFS='.' arr=(${az_domain})
IFS=${ifs}

az_localaz=${arr[0]}
az_localdz=${arr[1]}
az_region=${az_localaz}"."${az_localdz}

. /root/adminrc

echo "#!/usr/bin/env bash" > ${RUN_SCRIPT}
echo ". /root/adminrc" >> ${RUN_SCRIPT}

cinder_service_list=`cinder service-list | grep ${az_region} | awk -F"|" '{print $2}'`

for service in `echo ${cinder_service_list}`
do
    echo cinder service-enable ${az_region} ${service} >> ${RUN_SCRIPT}
done

nova_service_list=`nova service-list | grep ${az_region} | awk -F "|" '{print $3}'`
for service in `echo ${nova_service_list}`
do
    echo nova service-enable ${az_region} ${service} >> ${RUN_SCRIPT}
done

sh ${RUN_SCRIPT} > ${RUN_LOG} 2>&1
