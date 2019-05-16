#!/usr/bin/env python

#pyvmomi script to attach a disk from a VM
#author : Chandan Hegde (chandanhegden@gmail.com)

from __future__ import print_function

import atexit
import argparse
import getpass
from tools import tasks
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

    parser.add_argument('-ds' ,required=True,
                        help='Datastore which backs the virtual storage object')

    parser.add_argument('-v', '--vmname', required=True,
                        help='Name of the VirtualMachine on which you want to add the vmdk')

    parser.add_argument('-vdid', '--vDiskId', required=True,
                        help='virtual disk identifier of the disk which you want to attach')

    parser.add_argument('-controllerkey',
                        help='Key of the controller the disk will connect to')

    parser.add_argument('-unitnumber',
                        help='The unit number of the attached disk on its controller')

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


def Attach_vmdk(si, content, vm_obj, vdid, ds, controllerKey, unitNumber):

    id_object = vim.vslm.ID()
    id_object.id = vdid

    ds_obj = get_obj(content, [vim.Datastore], ds)

    print("##The ds is %s " % ds_obj)

    ##Attaching the disk with the vDiskId
    print("##Attaching the disk to  whose identifier is %s" % (vdid))
    attach_disk_task = vm_obj.AttachDisk_Task(id_object,ds_obj, int(controllerKey) , int(unitNumber) )
    tasks.wait_for_tasks(si,[attach_disk_task])
    print("##Attached the disk")


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
            print("##The VM object found. The argument disk is %s"%(args.ds))
            output = Attach_vmdk(si, content, vm_obj, args.vDiskId, args.ds, args.controllerkey , args.unitnumber)

    except Exception as e:
        print(e)


if __name__ == "__main__":
    main()
