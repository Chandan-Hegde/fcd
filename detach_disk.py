#!/usr/bin/env python3

#pyvmomi script to detach a disk from a VM
#author : Chandan Hegde (chandanhegden@gmail.com)

from __future__ import print_function

import atexit
import argparse
import getpass
from tools import  tasks
from pyVmomi import vim
from pyVim.connect import SmartConnectNoSSL, Disconnect


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
                        action='store',
                        help='Password to use when connecting to host')

    parser.add_argument('-v', '--vmname', required=True,
                        help='Name of the VirtualMachine you want to change.')
    parser.add_argument('-d', '--disk-number', required=True,
                        help='Disk number to change mode.')

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


def find_disk(content, vm_obj, disk_label ):

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev
            vdisk_id = virtual_disk_device.vDiskId.id
            ds_moid = virtual_disk_device.backing.datastore
            ds_moid_string = str(ds_moid)
            disk_backed_datastore = virtual_disk_device.backing.datastore.info.name

    # if virtual disk is not found
    if not virtual_disk_device:
        raise RuntimeError("##Virtual {} could not be found".format(disk_label))

    print("##vDiskId of %s  is %s is " % (disk_label, vdisk_id))
    #print ("###The datastore MoId corresponing to the datastore backed by this disk is %s"%( (ds_moid_string.split(":")[1])[:-1] ) )

    #return  [ vdisk_id, (ds_moid_string.split(":")[1])[:-1] ] ;

    return  [vdisk_id, disk_backed_datastore]


def Detach_vmdk(si, content, vm_obj, disk_number, disk_prefix_label='Hard disk '):

    disk_label = disk_prefix_label + str(disk_number)
    virtual_disk_device = None

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

    # if virtual disk is not found
    if not virtual_disk_device:
        raise RuntimeError("##Virtual {} could not be found".format(disk_label))


    list_vdiskid_ds = find_disk(content, vm_obj, disk_label)

    try:
        id_object = vim.vslm.ID()
        id_object.id = list_vdiskid_ds[0]

    ##Detaching the disk with the vDisId
        print("##Detaching the disk %s whose identifier is %s"%(disk_label, list_vdiskid_ds[1]))
        detach_disk_task = vm_obj.DetachDisk_Task(id_object)
        tasks.wait_for_tasks(si,[detach_disk_task])
        print("##Detached")

    except Exception as e:
        print("##Exception occured while detaching the disk %s"%(e))


def main():
    args = get_args()
    si = SmartConnectNoSSL(host=args.host,
                           user=args.user,
                           pwd=args.password,
                           port=int(args.port))
    atexit.register(Disconnect, si)

    content = si.RetrieveContent()
    print("##Searching for VM %s" % (args.vmname))
    vm_obj = get_obj(content, [vim.VirtualMachine], args.vmname)


    try:
        if vm_obj:
            Detach_vmdk(si, content, vm_obj, args.disk_number)
            print("##The Disk %s is detached from the virtual machine %s..." % (args.disk_number, args.vmname))

    except Exception as e:
        print(e)

if __name__ == "__main__":
    main()
