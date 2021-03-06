#!/usr/bin/env python

#################################################################################################################################
#
#
#pyvmomi script to make disk as FCD
#author : Chandan Hegde (chandanhegden@gmail.com)
#
#
#################################################################################################################################

from __future__ import print_function

import atexit
import argparse
import getpass
from tools import cli, tasks
from pyVmomi import vim
from pyVim.connect import SmartConnectNoSSL, Disconnect
from termcolor import colored


def get_args():
    parser = argparse.ArgumentParser(description='Process args for retrieving all the Virtual Machines')

    parser.add_argument('-s', '--host',
                        required=True,
                        action='store',
                        help='Remote host to connect to')
    parser.add_argument('-o', '--port',
                        type=int,
                        default=443,
                        action='store',
                        help='Port to connect on')
    parser.add_argument('-u', '--user',
                        required=True,
                        action='store',
                        help='User name to use when connecting to host')
    parser.add_argument('-p', '--password',
                        required=False,
                        action='store',
                        help='Password to use when connecting to host')

    parser.add_argument('-dcname', '--datacenter',
                        required=True,
                        help='DataCenter Name')

    parser.add_argument('-vm', '--vmname', required=True,
                        help='Name of the VirtualMachine you want to change.')

    parser.add_argument('-d', '--diskNumber', required=True,
                        help='Disk number to promote to FCD. Can be comma separated values like -d 1,2,3')

    args = parser.parse_args()

    if not args.password:
        args.password = getpass.getpass(
            prompt='Enter password for host %s and user %s: ' %
                   (args.host, args.user))
    return args


def get_obj(content, vim_type, name):
    obj = None

    container = content.viewManager.CreateContainerView(content.rootFolder, vim_type, True)

    for c in container.view:
        if c.name == name:
            obj = c
            break
    return obj


def build_paramters(si, ds, vm, vmdk_file, vc_name, dc_name):
    # print("###DC name in build_parameter fun is %s " % dc_name)

    l = vmdk_file.split("/")

    path_parameter = "https://" + vc_name + "/folder/" + vm + "/" + l[len(l) - 1] + "?dcPath=" + dc_name + "&dsName=" + ds
    # print("###Path Parameter : %s" % path_parameter)

    return path_parameter


#Module to promote the virtual disk as FCD
def mkfcd(vc_name, si, dc_name, content, vm_obj, disk_number, disk_prefix_label='Hard disk '):

    disk_label = disk_prefix_label + str(disk_number)
    virtual_disk_device = None

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

    # if virtual disk is not found
    if not virtual_disk_device:
        print("##Virtual {} could not be found".format(disk_label))
        return False
        # raise RuntimeError("##Virtual {} could not be found".format(disk_label))

    # checkigng the disk details
    if hasattr(virtual_disk_device.backing, 'fileName'):
        datastore = virtual_disk_device.backing.datastore
        if datastore:
            summary = {'capacity': datastore.summary.capacity,
                       'freeSpace': datastore.summary.freeSpace,
                       'file system': datastore.summary.type,
                       'url': datastore.summary.url}
            for key, val in summary.items():
                # print(u"            {0}: {1}".format(key, val))
                if key == 'url':
                    path_to_disk = val

        path_to_disk += virtual_disk_device.backing.fileName

        # print("###DC name in mkfcd fun is %s " % dc_name)
        parameter_for_fcd_disk = build_paramters(si, datastore.name, vm_obj.name, virtual_disk_device.backing.fileName, vc_name, dc_name)
        # print("###Parameter to fcd disk %s" % parameter_for_fcd_disk)

        #Registewring the disk as first class
        vstorage = content.vStorageObjectManager.RegisterDisk(parameter_for_fcd_disk)

        print("##The id is %s" % (vstorage.config.id.id))

        # print("##The data store MOID is %s" % vstorage.config.backing.datastore)

        #keeping last annotation as a buffer
        previous_annotation = vm_obj.summary.config.annotation

        #setting annotation
        spec = vim.vm.ConfigSpec()
        spec.annotation = previous_annotation + "Disk"+str(disk_number) + ":" + str(vstorage.config.id.id) + "\n"
        task = vm_obj.ReconfigVM_Task(spec)
        tasks.wait_for_tasks(si, [task])
        print("##Added the id annotation to VM")

    return True


def main():
    args = get_args()
    si = SmartConnectNoSSL(host=args.host,
                           user=args.user,
                           pwd=args.password,
                           port=int(args.port))
    atexit.register(Disconnect, si)

    content = si.RetrieveContent()
    print("###Searching for VM %s" % args.vmname)
    vm_obj = get_obj(content, [vim.VirtualMachine], (args.vmname))

    if vm_obj:
        print("###Found VM %s\n" % args.vmname)
        disk_numbers = args.diskNumber.split(',')

        for n in disk_numbers:
            try:
                print("###Attempting to promote vDisk %s into FCD" % n)
                fcd_task = mkfcd(args.host, si, args.datacenter, content, vm_obj, n)
                if fcd_task is True:
                    print(colored("###The Hard Disk %s is promoted to FCD", "green") % n)
                else:
                    print(colored("###vDisk %s is not promoted to FCD", "red") % n)
            except Exception as e:
                print(colored("###Exception in making disk as FCD %s ", "red") % e.msg)
            print()
    else:
        print("###VM {} is not found".format(args.vmname))


if __name__ == "__main__":
    main()
