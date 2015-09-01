#!/bin/sh
#2015.8.18 test ok

LOCAL_CONFIG=/etc/ipsec.d/ipsec.local.conf

add_tunnel_config() {
    tunnel_name=$1
    left=$2
    leftsubnet=$3
    right=$4
    rightsubnet=$5

    echo '* *: PSK "K8jsjm4n2dkkfy8ZTJue3iti6NbhsMCFu94zKvLw88z5Tqtkhh37gfujnE4hxwvn"' > /etc/ipsec.secrets
	
    flag="#add for ${tunnel_name}#"
    cat  >> ${LOCAL_CONFIG} <<CONFIG
${flag}
conn ${tunnel_name}
	type=tunnel
	authby=secret
	left=%defaultroute
	leftid=${left}
	leftsubnet=${leftsubnet}
	leftnexthop=%defaultroute
	right=${right}
	rightid=${right}
	rightsubnet=${rightsubnet}
	rightnexthop=%defaultroute
	pfs=yes
	auto=add
${flag}
CONFIG
}

{  [ -f ${LOCAL_CONFIG} ] && awk -v d=${1} '{e+=/^conn/&&$2==d}END{if(!e) exit 1}' ${LOCAL_CONFIG} ; } && { echo "exist"; true; } || { add_tunnel_config $1 $2 $3 $4 $5; }
