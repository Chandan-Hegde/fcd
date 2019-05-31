#!/usr/bin/env python

#######################################################################################################
#
#
#pyvmomi script to create, delete, revert and view chain of FCD backed snapshot.
#author : Chandan Hegde (chandanhegden@gmail.com)
#
#
#######################################################################################################

from __future__ import print_function

import atexit
import argparse
import getpass
from tools import cli, tasks
from pyVmomi import vim
from pyVim.connect import SmartConnectNoSSL, Disconnect
import detach_disk, attach_disk
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
                        action='store',
                        help='Password to use when connecting to host')

    parser.add_argument('-d', '--disk-number', required=False,
                        help='Disk number to promote to FCD. Can be comma separated values like -d 1,2,3 for creating the snapshot')

    parser.add_argument('-ds', '--dataStore', required=False,
                        help='Datastore which backs the virtual storage object')
    parser.add_argument('-description', required=False,
                        help='Description of the snapshot taken')
    parser.add_argument('-op', '--operation',required=True,
                        choices = ['create', 'delete','view','revert'],
                        help='The operation that you want to perform')
    parser.add_argument('-snid',required=False,
                        help='Snapshot id to which you need to revert to or delete')

    parser.add_argument('-vm', '--vmname', required=False,
                        help='Name of the VirtualMachine you want to change.')

    parser.add_argument('-vDiskId', '--virtualDiskId',
                        required=False,
                        help='vDiskId of FCD Disk')


    args = parser.parse_args()

    if not args.password:
        args.password = getpass.getpass(
            prompt='Enter password for host %s and user %s: ' %
                   (args.host, args.user))
    return args


#Get the vim object
def get_obj(content, vim_type, name):
    obj = None

    container = content.viewManager.CreateContainerView(content.rootFolder, vim_type, True)

    for c in container.view:
        if c.name == name:
            obj = c
            break
    return obj


#find the disk
def find_disk(content, vm_obj, disk_label , ):

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev
            vdisk_id = virtual_disk_device.vDiskId.id
            ds_moid = virtual_disk_device.backing.datastore
            ds_moid_string = str(ds_moid)
            disk_backed_datastore = virtual_disk_device.backing.datastore.info.name
            controllerKey = virtual_disk_device.controllerKey
            unitNumber = virtual_disk_device.unitNumber

    # if virtual disk is not found
    if not virtual_disk_device:
        raise RuntimeError("##Virtual {} could not be found".format(disk_label))

    # print("##vDiskId of %s is %s" % (disk_label, vdisk_id))

    return [vdisk_id, disk_backed_datastore , controllerKey , unitNumber]


#To create the snapshot
def create_snapshot(vc_name, si, content, vm_obj,  dn , description, disk_prefix_label='Hard disk '):

    disk_numbers = dn.split(',')

    for n in disk_numbers:
        try:
            disk_label = disk_prefix_label + str(n)
            virtual_disk_device = None

            # find the disk device
            for dev in vm_obj.config.hardware.device:
                if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                    virtual_disk_device = dev

                    if virtual_disk_device.vDiskId is None:
                        print(colored("##The Hard Disk %s should be promoted to FCD before Taking FCD level Snapshot.", "green") % n)

            # if virtual disk is not found
            if not virtual_disk_device:
                raise RuntimeError("##Virtual {} could not be found".format(disk_label))

            list_vdiskid_ds = find_disk(content, vm_obj,
                                        disk_label)  # return is list with two values one is vdisk id and other is datastore name

            id_object = vim.vslm.ID()
            id_object.id = list_vdiskid_ds[0]

            ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])

            # snapshot taken with the vstorageobjectmanager api
            snapshot_task = content.vStorageObjectManager.VStorageObjectCreateSnapshot_Task(id_object, ds_obj,
                                                                                            description)

            # calling wait_for_task module to monitor the task
            tasks.wait_for_tasks(si, [snapshot_task])

            print(colored("##Snapshot taken successfully on disk %s. Task id : %s", "green") % (n, snapshot_task))

        except Exception as e:
            print(colored("##Exception in taking snapshot %s ", "red") % (e.msg))


#To view the snapshot
def view_snapshot(content, vm_obj,  dn, disk_prefix_label='Hard disk '):

    print("<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Snapshots at VM level >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\n")

    if vm_obj.snapshot:
        snap_info = vm_obj.snapshot

        tree = snap_info.rootSnapshotList
        while tree[0].childSnapshotList is not None:
            print("### Name : {0}     ===>    Created Time : {1} | Snapshot State : {2} | Description : {3}".format(
                tree[0].name, tree[0].createTime, tree[0].state, tree[0].description))
            if len(tree[0].childSnapshotList) < 1:
                break
            tree = tree[0].childSnapshotList
    else:
        print("### No Snapshots found for VM {} at VM layer\n\n\n".format(vm_obj.name))


    print("\n\n<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Snapshots at FCD level >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")

    if dn:
        disk_numbers = dn.split(',')
        for n in disk_numbers:
            try:
                disk_label = disk_prefix_label + str(n)
                virtual_disk_device = None

                # find the disk device
                for dev in vm_obj.config.hardware.device:
                    if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                        virtual_disk_device = dev

                # if virtual disk is not found
                if not virtual_disk_device:
                    raise RuntimeError("##Virtual {} could not be found".format(disk_label))

                list_vdiskid_ds = find_disk(content, vm_obj,
                                            disk_label)  # Returned values are vdiskid, datastore, controllerKey and unitNumber

                id_object = vim.vslm.ID()
                id_object.id = list_vdiskid_ds[0]  # virtual disk identifier

                ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])  # datastore object is returned

                snapshot = content.vStorageObjectManager.RetrieveSnapshotInfo(id_object, ds_obj)

                print("\n\n##The snapshots of FCD disk#%s are :" % n)
                count = 1
                for sn in snapshot.snapshots:
                    print(
                        colored("\t\t#%s -> description : %s, creation Time : %s , snapshot identifier : %s ", "green") % (
                            count, sn.description, sn.createTime, sn.id.id))
                    count = count + 1


            except Exception as e:
                print(colored("##Exception in viewing the snapshot : %s ", "red") % (e.msg))
            print()
    else:
        vmdks = []
        try:
            # find the disk device
            for dev in vm_obj.config.hardware.device:
                if isinstance(dev, vim.vm.device.VirtualDisk):
                    vmdks.append(dev)

            for virtual_disk_device in vmdks:
                id_object = virtual_disk_device.vDiskId  # virtual disk identifier
                ds_obj = virtual_disk_device.backing.datastore  # datastore object is returned

                if id_object:
                    snapshot = content.vStorageObjectManager.RetrieveSnapshotInfo(id_object, ds_obj)

                    print("\n##The snapshots of FCD %s are :" % virtual_disk_device.deviceInfo.label)
                    count = 1
                    for sn in snapshot.snapshots:
                        print(colored("\t\t#%s -> description : %s, creation Time : %s , snapshot identifier : %s ","green") % (count, sn.description, sn.createTime, sn.id.id))
                        count = count + 1

        except Exception as e:
            print(colored("##Exception in viewing the snapshot : %s ", "red") % (e.msg))


def view_vDisk_Snapshot(content, id, ds):
    """ This module helps in viewing Snapshot with vDiskId instead of referencing VM as FCD is independent entity"""

    # Get the datastore
    ds_obj = get_obj(content, [vim.Datastore], ds)

    vDiskId_object = vim.vslm.ID()
    vDiskId_object.id = id

    snapshot = content.vStorageObjectManager.RetrieveSnapshotInfo(vDiskId_object, ds_obj)

    print("<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Snapshots at FCD level >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")

    print("\n##The snapshots of FCD disk with id %s are :" % id)
    count = 1
    for sn in snapshot.snapshots:
        print(colored("\t\t#%s -> description : %s, creation Time : %s , snapshot identifier : %s ", "green") % (count, sn.description, sn.createTime, sn.id.id))
        count = count + 1
    print("\n")


#Delete the Snapshot
def delete_snapshot(si, content, vm_obj,  dn , snid, disk_prefix_label='Hard disk '):
    try:

        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError("##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj,
                                    disk_label)  # return is list with two values one is vdisk id and other is datastore name


        id_object1 = vim.vslm.ID()
        id_object1.id = list_vdiskid_ds[0]

        id_object2 = vim.vslm.ID()
        id_object2.id = snid

        ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])

        snapshot_task = content.vStorageObjectManager.DeleteSnapshot_Task(id_object1, ds_obj, id_object2)
        tasks.wait_for_tasks(si,[snapshot_task])

        print(colored("##Deleted the snapshot with snapshot id  %s. Task id : %s ","green")%(snid,snapshot_task))

    except Exception as e:
        print(colored("##Exception in deleting the snapshot is : %s ","red")%(e.msg))


#Revert Snapshot
def revert_snapshot(si, content, vm_obj,  dn , snid, disk_prefix_label='Hard disk '):
    try:

        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError("##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj,
                                    disk_label)  # return is list with two values one is vdisk id and other is datastore name

        id_object1 = vim.vslm.ID()
        id_object1.id = list_vdiskid_ds[0]

        id_object2 = vim.vslm.ID()
        id_object2.id = snid

        ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])


        #Detaching the disk before revert as that's the design of FCD 6.7 atleast!!!!
        print(colored("##Detaching the disk %s before the revert.","green")%(disk_label))
        detach_disk.Detach_vmdk(si, content,vm_obj, dn)
        print(colored("##Detaching is done and I have captured the virtual disk id, datastore name, controllerKey and unitNumber as %s, %s, %s and %s which I need for attaching the device back.","green")%(list_vdiskid_ds[0], list_vdiskid_ds[1], list_vdiskid_ds[2], list_vdiskid_ds[3]))


        #I am reverting with the FCD api
        snapshot_task = content.vStorageObjectManager.RevertVStorageObject_Task(id_object1, ds_obj, id_object2)
        tasks.wait_for_tasks(si,[snapshot_task])
        print(colored("##Reverted to the snapshot with snapshot id  %s. Task id : %s ","green")%(snid,snapshot_task))

        ##Look, I am immediately attaching it back...
        print(colored("##Attaching the disk %s back","green")%(disk_label))
        attach_disk.Attach_vmdk(si, content, vm_obj, list_vdiskid_ds[0] ,list_vdiskid_ds[1],list_vdiskid_ds[2] , list_vdiskid_ds[3])
        print (colored("##The disk %s is attached back.","green")%(disk_label))
        print(colored("##Though the disk is attached back, the operation needs reboot as the snapshot is not an in-memory snapshot","green"))

    except Exception as e:
        print(colored("##Exception in Reverting to the snapshot is : %s ","red")%(e.msg))

        ## I am attaching disk that is detached back, though snapshot revert process raised an exception
        print(colored("##Attaching the disk %s back","green")%(disk_label))
        attach_disk.Attach_vmdk(si, content, vm_obj, list_vdiskid_ds[0] ,list_vdiskid_ds[1],list_vdiskid_ds[2] , list_vdiskid_ds[3])
        print (colored("##The disk %s is attached back.","green")%(disk_label))
        print(colored("##Could not revert to snapshot in time instant because of %s exception, but attached the disk back again which was detached for revert operation","green")%(e.msg))


def main():
    args = get_args()


    si = SmartConnectNoSSL(host=args.host,
                           user=args.user,
                           pwd=args.password,
                           port=int(args.port))
    atexit.register(Disconnect, si)

    content = si.RetrieveContent()

    if args.vmname:
        print("##Searching for VM %s" % (args.vmname))
        vm_obj = get_obj(content, [vim.VirtualMachine], args.vmname)

        if vm_obj:
            print("###Found VM %s\n" % args.vmname)
            if args.operation == 'create':
                hw_version = (vm_obj.config.version).split('-')[1]
                if int(hw_version) >= 13:
                    if args.description == '':
                        print(colored("###The snapshot needs description", "red"))
                    else:
                        if args.disk_number:
                            create_snapshot(args.host, si, content, vm_obj, args.disk_number, args.description)
                else:
                    print(
                        "###Taking snapshot on VM whoes hardware version is less than VMX-13 does not allow revert operation. Hence, better not to go with FCD level snapshot  here.")

            if args.operation == 'view':
                view_snapshot(content, vm_obj, args.disk_number)
                print("\n\n")

            if args.operation == 'delete':
                if args.disk_number:
                    delete_snapshot(si, content, vm_obj, args.disk_number, args.snid)

            if args.operation == 'revert':
                if args.disk_number:
                    revert_snapshot(si, content, vm_obj, args.disk_number, args.snid)

        else:
            print("###VM {} is not found\n".format(args.vmname))
    else:
        if args.operation == 'view':
            if args.virtualDiskId and args.dataStore:
                view_vDisk_Snapshot(content,args.virtualDiskId, args.dataStore)
            else:
                print("Please provide vDisk Id, DataStore or VM name")
        else:
            print("Please provide the VM name\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python

"""
pyvmomi script to create, delete, revert and view chain of FCD backed snapshot.
author : Chandan Hegde (chandanhegden@gmail.com)
"""

from __future__ import print_function

import atexit
import argparse
import getpass
from tools import cli, tasks
from pyVmomi import vim
from pyVim.connect import SmartConnectNoSSL, Disconnect
import detach_disk, attach_disk
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
                        action='store',
                        help='Password to use when connecting to host')

    parser.add_argument('-d', '--disk-number', required=True,
                        help='Disk number to change mode.')

    parser.add_argument('-ds', required=False,
                        help='Datastore which backs the virtual storage object')
    parser.add_argument('-description', required=False,
                        help='Description of the snapshot taken')
    parser.add_argument('-op','--operation',required=True,
                        choices = ['create', 'delete','view','revert'],
                        help='The operation that you want to perform')
    parser.add_argument('-snid',required=False,
                        help='Snapshot id to which you need to revert to or delete')

    parser.add_argument('-v', '--vmname', required=True,
                        help='Name of the VirtualMachine you want to change.')

    args = parser.parse_args()

    if not args.password:
        args.password = getpass.getpass(
            prompt='Enter password for host %s and user %s: ' %
                   (args.host, args.user))
    return args


#Get the vim object
def get_obj(content, vim_type, name):
    obj = None

    container = content.viewManager.CreateContainerView(content.rootFolder, vim_type, True)

    for c in container.view:
        if c.name == name:
            obj = c
            break
    return obj


#find the disk
def find_disk(content, vm_obj, disk_label , ):

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev
            vdisk_id = virtual_disk_device.vDiskId.id
            ds_moid = virtual_disk_device.backing.datastore
            ds_moid_string = str(ds_moid)
            disk_backed_datastore = virtual_disk_device.backing.datastore.info.name
            controllerKey = virtual_disk_device.controllerKey
            unitNumber = virtual_disk_device.unitNumber

    # if virtual disk is not found
    if not virtual_disk_device:
        raise RuntimeError("##Virtual {} could not be found".format(disk_label))

    print("##vDiskId of %s is %s" % (disk_label, vdisk_id))

    return [vdisk_id, disk_backed_datastore , controllerKey , unitNumber]


#To create the snapshot
def create_snapshot(si, content, vm_obj,  dn , description, disk_prefix_label='Hard disk '):

    try:
        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError("##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj, disk_label  )  #return is list with two values one is vdisk id and other is datastore name

        id_object = vim.vslm.ID()
        id_object.id = list_vdiskid_ds[0]

        ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])

        #snapshot taken with the vstorageobjectmanager api
        snapshot_task = content.vStorageObjectManager.VStorageObjectCreateSnapshot_Task(id_object , ds_obj, description)

        #calling wait_for_task module to monitor the task
        tasks.wait_for_tasks(si,[snapshot_task])

        print(colored("##Snapshot taken successfully. Task id : %s", "green")%(snapshot_task))

    except Exception as e:
        print(colored("##Exception in taking snapshot is %s ", "red") % (e))


#To view the snapshot
def view_snapshot(content, vm_obj,  dn, disk_prefix_label='Hard disk '):

    print("<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Snapshots at VM level >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")

    if vm_obj.snapshot:
        snap_info = vm_obj.snapshot

        tree = snap_info.rootSnapshotList
        while tree[0].childSnapshotList is not None:
            print("### Name : {0}     ===>    Created Time : {1} | Snapshot State : {2} | Description : {3}".format(
                tree[0].name, tree[0].createTime, tree[0].state, tree[0].description))
            if len(tree[0].childSnapshotList) < 1:
                break
            tree = tree[0].childSnapshotList
    else:
        print("### No Snapshots found for VM {} at VM layer".format(vm_obj.name))


    print("<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Snapshots at Improved vDISK/ FCD level >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")

    try:
        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError("##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj, disk_label)  #Returned values are vdiskid, datastore, controllerKey and unitNumber

        id_object = vim.vslm.ID()
        id_object.id = list_vdiskid_ds[0]  #virtual disk identifier

        ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])  #datastore object is returned

        snapshot = content.vStorageObjectManager.RetrieveSnapshotInfo(id_object, ds_obj)

        print(colored("##The snapshots are :","green"))
        count = 1
        for sn in snapshot.snapshots:
            print(colored("#%s -> description : %s, creation Time : %s , snapshot identifier : %s ","green")%(count,sn.description,sn.createTime,sn.id.id))
            count = count + 1

    except Exception as e:
        print(colored("##Exception in viewing the snapshot : %s ","red")%(e))


#Delete the Snapshot
def delete_snapshot(si , content, vm_obj,  dn , snid, disk_prefix_label='Hard disk '):
    try:

        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError("##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj,
                                    disk_label)  # return is list with two values one is vdisk id and other is datastore name


        id_object1 = vim.vslm.ID()
        id_object1.id = list_vdiskid_ds[0]

        id_object2 = vim.vslm.ID()
        id_object2.id = snid

        ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])

        snapshot_task = content.vStorageObjectManager.DeleteSnapshot_Task(id_object1, ds_obj, id_object2)
        tasks.wait_for_tasks(si,[snapshot_task])

        print(colored("##Deleted the snapshot with snapshot id  %s. Task id : %s ","green")%(snid,snapshot_task))

    except Exception as e:
        print(colored("##Exception in deleting the snapshot is : %s ","red")%(e))


#Revert Snapshot
def revert_snapshot(si, content, vm_obj,  dn , snid, disk_prefix_label='Hard disk '):
    try:

        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError("##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj,
                                    disk_label)  # return is list with two values one is vdisk id and other is datastore name

        id_object1 = vim.vslm.ID()
        id_object1.id = list_vdiskid_ds[0]

        id_object2 = vim.vslm.ID()
        id_object2.id = snid

        ds_obj = get_obj(content, [vim.Datastore], list_vdiskid_ds[1])


        #Detaching the disk before revert as that's the design of FCD 6.7 atleast!!!!
        print(colored("##Detaching the disk %s before the revert.","green")%(disk_label))
        detach_disk.Detach_vmdk(si, content,vm_obj, dn)
        print(colored("##Detaching is done and I have captured the virtual disk id, datastore name, controllerKey and unitNumber as %s, %s, %s and %s which I need for attaching the device back.","green")%(list_vdiskid_ds[0], list_vdiskid_ds[1], list_vdiskid_ds[2], list_vdiskid_ds[3]))


        #I am reverting with the FCD api
        snapshot_task = content.vStorageObjectManager.RevertVStorageObject_Task(id_object1, ds_obj, id_object2)
        tasks.wait_for_tasks(si,[snapshot_task])
        print(colored("##Reverted to the snapshot with snapshot id  %s. Task id : %s ","green")%(snid,snapshot_task))

        ##Look, I am immediately attaching it back...
        print(colored("##Attaching the disk %s back","green")%(disk_label))
        attach_disk.Attach_vmdk(si, content, vm_obj, list_vdiskid_ds[0] ,list_vdiskid_ds[1],list_vdiskid_ds[2] , list_vdiskid_ds[3])
        print (colored("##The disk %s is attached back.","green")%(disk_label))
        print(colored("##Though the disk is attached back, the operation needs reboot as the snapshot is not an in-memory snapshot","green"))

    except Exception as e:
        print(colored("##Exception in Reverting to the snapshot is : %s ","red")%(e))

        ## I am attaching disk that is detached back, though snapshot revert process raised an exception
        print(colored("##Attaching the disk %s back","green")%(disk_label))
        attach_disk.Attach_vmdk(si, content, vm_obj, list_vdiskid_ds[0] ,list_vdiskid_ds[1],list_vdiskid_ds[2] , list_vdiskid_ds[3])
        print (colored("##The disk %s is attached back.","green")%(disk_label))
        print(colored("##Could not revert to snapshot in time instant because of %s exception, but attached the disk back again which was detached for revert operation","green")%(e.msg))


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

    if args.operation == 'create':
        if args.description == '':
            print(colored("##The snapshot needs description","red"))
        else:
            create_snapshot(si, content, vm_obj,  args.disk_number, args.description)

    if args.operation == 'view':
        view_snapshot(content, vm_obj,  args.disk_number)

    if args.operation == 'delete':
        delete_snapshot(si, content, vm_obj,  args.disk_number ,args.snid )

    if args.operation == 'revert':
        revert_snapshot(si, content, vm_obj,  args.disk_number ,args.snid)


if __name__ == "__main__":
    main()

