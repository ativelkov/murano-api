#!/bin/bash
#    Copyright (c) 2014 Mirantis, Inc.
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
RUN_DIR=$(cd $(dirname "$0") && pwd)
INC_FILE="$RUN_DIR/common.inc"
if [ -f "$INC_FILE" ]; then
    source "$INC_FILE"
else
    echo "Can't load \"$INC_FILE\" or file not found, exiting!"
    exit 1
fi
#
DAEMON_NAME="murano-api"
DAEMON_USER="murano"
DAEMON_GROUP="murano"
DAEMON_CFG_DIR="/etc/murano"
DAEMON_LOG_DIR="/var/log/murano"
LOGFILE="/tmp/${DAEMON_NAME}_install.log"
DAEMON_DB_CONSTR="sqlite:///$DAEMON_CFG_DIR/$DAEMON_NAME.sqlite"
common_pkgs="wget git make gcc python-pip python-setuptools"
# Distro-specific package namings
debian_pkgs="python-dev python-mysqldb libxml2-dev libxslt1-dev libffi-dev mysql-client"
redhat_pkgs="python-devel MySQL-python libxml2-devel libxslt-devel libffi-devel mysql"
#
get_os
eval req_pkgs="\$$(lowercase $DISTRO_BASED_ON)_pkgs"
REQ_PKGS="$common_pkgs $req_pkgs"

function install_prerequisites()
{
    retval=0
    _dist=$(lowercase $DISTRO_BASED_ON)
    if [ $_dist = "redhat" ]; then
        yum repolist | grep -qoE "epel"
        if [ $? -ne 0 ]; then
            log "Enabling EPEL6..."
            rpm -ivh http://dl.fedoraproject.org/pub/epel/6/x86_64/epel-release-6-8.noarch.rpm >> $LOGFILE 2>&1
            if [ $? -ne 0 ]; then
                log "... can't enable EPEL6, exiting!"
                retval=1
                return $retval
            fi
        fi
        yum --quiet makecache
    fi
    for pack in $REQ_PKGS
    do
        find_or_install "$pack"
        if [ $? -eq 1 ]; then
            retval=1
            break
        else
            retval=0
        fi
    done
    return $retval
}
function make_tarball()
{
    retval=0
    log "Preparing tarball package..."
    setuppy="$RUN_DIR/setup.py"
    if [ -e "$setuppy" ]; then
        chmod +x $setuppy
        rm -rf $RUN_DIR/*.egg-info
        cd $RUN_DIR && python $setuppy egg_info > /dev/null 2>&1
        if [ $? -ne 0 ];then
            log "...\"$setuppy\" egg info creation fails, exiting!!!"
            retval=1
            exit 1
        fi
        rm -rf $RUN_DIR/dist/*
        log "...\"setup.py sdist\" output will be recorded in \"$LOGFILE\""
        cd $RUN_DIR && $setuppy sdist >> $LOGFILE 2>&1
        if [ $? -ne 0 ];then
            log "...\"$setuppy\" tarball creation fails, exiting!!!"
            retval=1
            exit 1
        fi
        #TRBL_FILE=$(basename $(ls $RUN_DIR/dist/*.tar.gz | head -n 1))
        TRBL_FILE=$(ls $RUN_DIR/dist/*.tar.gz | head -n 1)
        if [ ! -e "$TRBL_FILE" ]; then
            log "...tarball not found, exiting!"
            retval=1
        else
            log "...success, tarball created as \"$TRBL_FILE\""
            retval=0
        fi
    else
        log "...\"$setuppy\" not found, exiting!"
        retval=1
    fi
    return $retval
}
function run_pip_install()
{
    find_pip
    retval=0
    tarball_file=${1:-$TRBL_FILE}
    log "Running \"$PIPCMD install $PIPARGS $tarball_file\" output will be recorded in \"$LOGFILE\""
    $PIPCMD install $PIPARGS $tarball_file >> $LOGFILE 2>&1
    if [ $? -ne 0 ]; then
        log "...pip install fails, exiting!"
        retval=1
        exit 1
    fi
    return $retval
}
function prepare_db()
{
    retval=0
    _dist=$(lowercase $DISTRO_BASED_ON)
    _mysql_db_name="murano_api"
    _mysql_db_user="muranoUser"
    _mysql_db_pass="$(genpass)"
    _mysql_root_pass="swordfish"
    _mysql_cmd="CREATE DATABASE IF NOT EXISTS $_mysql_db_name DEFAULT CHARACTER SET=utf8; GRANT ALL PRIVILEGES ON $_mysql_db_name.* TO '$_mysql_db_user'@'localhost' IDENTIFIED BY '$_mysql_db_pass'; GRANT ALL PRIVILEGES ON $_mysql_db_name.* TO '$_mysql_db_user'@'%' IDENTIFIED BY '$_mysql_db_pass';"
    mysql_packages="mysql-server"
    case $_dist in
        "debian")
            export DEBIAN_FRONTEND=noninteractive
            ;;
    esac
    log "Installing and configuring MySQL server..."
    for pack in $mysql_packages
    do
        find_or_install "$pack"
        _ret=$?
        if [ $_ret -eq 1 ]; then
            retval=1
            break
        elif [ $_ret -eq 2 ]; then
            log "MySQL server already installed, so create db manually with command like this!"
            log "$_mysql_cmd"
            break
        fi
        unset _ret
    done
    if [ -n "$DEBIAN_FRONTEND" ]; then
        unset DEBIAN_FRONTEND
    fi
    _mycfn=$(find /etc -name "my.cnf" | head -n 1)
    if [ $? -ne 0 ]; then
        log "Can't find \"my.cfn\" under \"/etc/...\""
        retval=1
        exit 1
    fi
    iniset 'mysqld' 'bind-address' '127.0.0.1' "$_mycfn"
    iniset 'mysqld' 'port' '3306' "$_mycfn"
    case $_dist in
        "debian")
            unset DEBIAN_FRONTEND
            service mysql restart
            ;;
        *)
            service mysqld restart
            ;;
    esac
    mysqladmin -u root password "$_mysql_root_pass"
    if [ $? -ne 0 ]; then
        log "Trying with default password..."
        mysqladmin -u root -p$_mysql_root_pass password "$_mysql_root_pass"
        if [ $? -ne 0 ]; then
            log "Can't set MySQL root user password, please run manually:"
            log "$_mysql_cmd"
            log "Set connection sting to \"mysql://$_mysql_db_user:$_mysql_db_pass@localhost:3306/$_mysql_db_name\" if you plan to use MySQL, sqlite will be used by default!"
            retval=1
            return $retval
        else
            log "...success, MySQL root password reset \"\$_mysql_root_pass value\""
        fi
    fi
    mysql -uroot -p$_mysql_root_pass -e "$_mysql_cmd"
    if [ $? -ne 0 ]; then
        log "DB creation fails, please run manually:"
        log "$_mysql_cmd"
        retval 1
    fi
    DAEMON_DB_CONSTR="mysql://$_mysql_db_user:$_mysql_db_pass@localhost:3306/$_mysql_db_name"
    return $retval
}
function inject_init()
{
    retval=0
    _dist=$(lowercase $DISTRO_BASED_ON)
    eval src_init_sctipt="$DAEMON_NAME-$_dist"
    _initscript="openstack-$DAEMON_NAME"
    cp -f "$RUN_DIR/etc/init.d/$src_init_sctipt" "/etc/init.d/$_initscript" || retval=$?
    chmod +x "/etc/init.d/$_initscript" || retval=$?
    iniset '' 'SYSTEM_USER' "$DAEMON_USER" "/etc/init.d/$_initscript"
    iniset '' 'DAEMON' "$(shslash $SERVICE_EXEC_PATH)" "/etc/init.d/$_initscript"
    case $_dist in
        "debian")
            update-rc.d $_initscript defaults || retval=$?
            update-rc.d $_initscript enable || retval=$?
            ;;
        *)
            chkconfig --add $_initscript || retval=$?
            chkconfig $_initscript on || retval=$?
            ;;
    esac
    return $retval
}
function purge_init()
{
    retval=0
    _dist=$(lowercase $DISTRO_BASED_ON)
    _initscript="openstack-$DAEMON_NAME"
    service $_initscript stop
    if [ $? -ne 0 ]; then
        retval=1
    fi
    case $_dist in
        "debian")
            update-rc.d  $_initscript disable
            update-rc.d -f $_initscript remove || retval=$?
            ;;
        *)
            chkconfig $_initscript off || retval=$?
            chkconfig --del $_initscript || retval=$?
            ;;
    esac
    rm -f "/etc/init.d/$_initscript" || retval=$?
    return $retval
}
function run_pip_uninstall()
{
    find_pip
    retval=0
    pack_to_del=$(is_py_package_installed "$DAEMON_NAME")
    if [ $? -eq 0 ]; then
        log "Running \"$PIPCMD uninstall $PIPARGS $DAEMON_NAME\" output will be recorded in \"$LOGFILE\""
        $PIPCMD uninstall $pack_to_del --yes >> $LOGFILE 2>&1
        if [ $? -ne 0 ]; then
            log "...can't uninstall $DAEMON_NAME with $PIPCMD"
            retval=1
        else
            log "...success"
        fi
    else
        log "Python package for \"$DAEMON_NAME\" not found"
    fi
    return $retval
}
function install_daemon()
{
    install_prerequisites || exit 1
    make_tarball || exit $?
    run_pip_install || exit $?
    add_daemon_credentials "$DAEMON_USER" "$DAEMON_GROUP" || exit $?
    log "Creating required directories..."
    mk_dir "$DAEMON_CFG_DIR" "$DAEMON_USER" "$DAEMON_GROUP" || exit 1
    mk_dir "$DAEMON_LOG_DIR" "$DAEMON_USER" "$DAEMON_GROUP" || exit 1
    log "Making sample configuration files at \"$DAEMON_CFG_DIR\""
    _src_conf_dir="$RUN_DIR/etc/murano"
    for file in $(ls $_src_conf_dir)
    do
        cp -f "$_src_conf_dir/$file" "$DAEMON_CFG_DIR/$file.sample"
        if [ ! -e "$DAEMON_CFG_DIR/$file" ]; then
            log "\"$DAEMON_CFG_DIR/$file\" exists."
            cp -f "$_src_conf_dir/$file" "$DAEMON_CFG_DIR/$file"
        fi
    done
    log "Setting log file and sqlite db placement..."
    iniset 'DEFAULT' 'log_file' "$(shslash $DAEMON_LOG_DIR/$DAEMON_NAME.log)" "$DAEMON_CFG_DIR/$DAEMON_NAME.conf"
    iniset 'database' 'connection' "$(shslash $DAEMON_DB_CONSTR)" "$DAEMON_CFG_DIR/$DAEMON_NAME.conf"
    log "Searching daemon in \$PATH..."
    get_service_exec_path || exit $?
    log "...found at \"$SERVICE_EXEC_PATH\""
    log "Installing SysV init script."
    inject_init || exit $?
    prepare_db
    if [ $? -eq 0 ]; then
        iniset 'database' 'connection' "$(shslash $DAEMON_DB_CONSTR)" "$DAEMON_CFG_DIR/$DAEMON_NAME.conf"
        log "...database configuration finished."
    fi
    log "Everything done, please, verify \"$DAEMON_CFG_DIR/$DAEMON_NAME.conf\", service created as \"openstack-murano-api\"."
}
function uninstall_daemon()
{
    log "Removing SysV init script..."
    purge_init || exit $?
    remove_daemon_credentials "$DAEMON_USER" "$DAEMON_GROUP" || exit $?
    run_pip_uninstall || exit $?
    log "Software uninstalled, daemon configuration files and logs located at \"$DAEMON_CFG_DIR\" and \"$DAEMON_LOG_DIR\"."
}
# Command line args'
COMMAND="$1"
case $COMMAND in
    install)
        rm -rf $LOGFILE
        log "Installing \"$DAEMON_NAME\" to system..."
        install_daemon
        ;;

    uninstall )
        log "Uninstalling \"$DAEMON_NAME\" from system..."
        uninstall_daemon
        ;;

    * )
        echo -e "Usage: $(basename "$0") [command] \nCommands:\n\tinstall - Install \"$DAEMON_NAME\" software\n\tuninstall - Uninstall \"$DAEMON_NAME\" software"
        exit 1
        ;;
esac