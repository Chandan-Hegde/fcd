#!/usr/bin/env python

#######################################################################################################
#
#
# pyvmomi script to create, delete, revert and view chain of FCD backed snapshot.
#
#
#######################################################################################################

from __future__ import print_function

import atexit
import argparse
import getpass
from tools import cli, tasks
from pyVmomi import vim, vmodl
import fcd_op
from pyVim.connect import SmartConnectNoSSL, Disconnect
import pdb  # python debugger


def get_args():
    parser = argparse.ArgumentParser(
        description='Process args for retrieving all the Virtual Machines')

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
                        action='store', help='User name to use when connecting to host')
    parser.add_argument('-p', '--password',
                        action='store', help='Password to use when connecting to host')
    parser.add_argument('-d', '--disk-number', required=False,
                        help='Disk number to promote to FCD. Can be comma separated values like -d 1,2,3 for creating the snapshot')
    parser.add_argument('-ds', '--dataStore', required=False,
                        help='Datastore which backs the virtual storage object')
    parser.add_argument('-description', required=False,
                        help='Description of the snapshot taken')
    parser.add_argument('-op', '--operation', required=True,
                        choices=['create', 'delete', 'view', 'revert'],
                        help='The operation that you want to perform')
    parser.add_argument('-snid', required=False,
                        help='Snapshot id to which you need to revert to or delete')
    parser.add_argument('-vm', '--vmname', required=False,
                        help='Name of the VirtualMachine you want to change.')
    parser.add_argument('-vDiskId', '--virtualDiskId',
                        required=False, help='vDiskId of FCD Disk')

    args = parser.parse_args()

    if not args.password:
        args.password = getpass.getpass(
            prompt='Enter password for host %s and user %s: ' %
                   (args.host, args.user))
    return args


def find_DCname(vm_obj):
    """
    Find the datacenter name of the virtuam machine
    """
    # find the Name of VMs DataCenter
    TempParent = vm_obj
    dc = vim.Datacenter

    while True:
        if (type(TempParent.parent) != dc):
            TempParent = TempParent.parent
        else:
            break

    dcname = TempParent.parent.name
    return dcname


def create_filter_spec(pc, objs, props, obj_type):
    """Creates the filter specification"""

    objSpecs = []
    for si_obj in objs:
        objSpec = vmodl.query.PropertyCollector.ObjectSpec(obj=si_obj)
        objSpecs.append(objSpec)
    filterSpec = vmodl.query.PropertyCollector.FilterSpec()
    filterSpec.objectSet = objSpecs
    propSet = vmodl.query.PropertyCollector.PropertySpec(all=False)
    propSet.type = obj_type
    for prop in props:
        (propSet.pathSet).append(prop)
    filterSpec.propSet = [propSet]
    return filterSpec


def filter_results(result, value):
    """Filter the result for the value"""

    for si_obj in result:
        if value in si_obj.propSet[0].val:
            return si_obj.obj
    return None


def Filter_Obj(ServiceInstance, objs, filter_propertys, filter_value, obj_type):
    """ Fileter the VM with it's name"""

    pc = ServiceInstance.content.propertyCollector
    filter_spec = create_filter_spec(pc, objs, filter_propertys, obj_type)
    options = vmodl.query.PropertyCollector.RetrieveOptions()
    result = pc.RetrieveProperties([filter_spec])
    si_obj = filter_results(result, filter_value)
    return si_obj


def get_obj(ServiceInstance, root, vim_type):
    """Create container view and search for object in it"""

    container = ServiceInstance.content.viewManager.CreateContainerView(root, vim_type,
                                                                        True)
    view = container.view
    container.Destroy()
    return view


def find_disk(content, vm_obj, disk_label):
    """ 
    Finds and return list of disk virtual disk id, disk_backed_datastore, controllerKey, unitNumber
    """

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

            id_object = virtual_disk_device.vDiskId

            # if FCD
            if id_object:
                vdisk_id = virtual_disk_device.vDiskId.id
            else:
                vdisk_id = None

            disk_backed_datastore = virtual_disk_device.backing.datastore.info.name
            controllerKey = virtual_disk_device.controllerKey
            unitNumber = virtual_disk_device.unitNumber

            return [vdisk_id, disk_backed_datastore, controllerKey, unitNumber]

    # if virtual disk is not found
    if not virtual_disk_device:
        raise RuntimeError(
            "##Virtual {} could not be found".format(disk_label))


def Attach_vmdk(si, content, vm_obj, vdid, ds, controllerKey, unitNumber):

    id_object = vim.vslm.ID()
    id_object.id = vdid

    # DataStores view stored in vms object
    dss = get_obj(si, si.content.rootFolder, [vim.Datastore])

    # Filtering
    filter_propertys = ["name"]
    filter_value = ds
    obj_type = vim.Datastore
    ds_obj = Filter_Obj(si, dss, filter_propertys, filter_value, obj_type)

    # Attaching the disk with the vDiskId
    attach_disk_task = vm_obj.AttachDisk_Task(
        id_object, ds_obj, int(controllerKey), int(unitNumber))

    tasks.wait_for_tasks(si, [attach_disk_task])
    print("###Attached the disk whoes identifier is {}".format(vdid))


def Detach_vmdk(si, content, vm_obj, disk_number, disk_prefix_label='Hard disk '):

    disk_label = disk_prefix_label + str(disk_number)
    virtual_disk_device = None

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

    # if virtual disk is not found
    if not virtual_disk_device:
        raise RuntimeError(
            "###Virtual {} could not be found".format(disk_label))

    list_vdiskid_ds = find_disk(content, vm_obj, disk_label)

    # If not an FCD then do not perform the detach operation and abort the program as it's difficult to attach it later
    if list_vdiskid_ds[0] is None:
        raise RuntimeError(
            "###{} is not an FCD, hence not performing detach operation".format(disk_label))

    try:
        id_object = vim.vslm.ID()
        id_object.id = list_vdiskid_ds[0]

        # Detaching the disk with the vDisId
        print("###Detaching the disk whose identifier is %s" %
              (list_vdiskid_ds[0]))
        detach_disk_task = vm_obj.DetachDisk_Task(id_object)
        tasks.wait_for_tasks(si, [detach_disk_task])
        print("###Detached")

    except Exception as e:
        print("###Exception occured while detaching the disk %s" % (e))


def create_snapshot(vc_name, si, content, vm_obj,  dn, description, disk_prefix_label='Hard disk '):
    """Create the FCD Disk snapshot """ 

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
                raise RuntimeError(
                    "##Virtual {} could not be found".format(disk_label))

            list_vdiskid_ds = find_disk(content, vm_obj,
                                        disk_label)  # return is list with two values one is vdisk id and other is datastore name

            # If not an FCD then do not perform the detach operation and abort the program as it's difficult to attach it later
            if list_vdiskid_ds[0] is None:
                print("{} is Not an FCD disk".format(disk_label))

                user_choice = input(
                    "\nDo you want to promote to FCD ? (yes/no)")

                if user_choice == "y" or user_choice == "yes" or user_choice == "Y":
                    dcname = find_DCname(vm_obj)
                    fcd_op.mkfcd(vc_name, si, dcname, content, vm_obj, n)

                    # calling this again so that I can get fresh copy of the fcd disk id
                    list_vdiskid_ds = find_disk(content, vm_obj,
                                                disk_label)  # return is list with two values one is vdisk id and other is datastore name

                elif user_choice == "n" or user_choice == "no" or user_choice == "N":
                    print("Cannot complete snapshot creation as the {} is not FCD disk".format(
                        disk_label))
                    return
                else:
                    print("Invalid input...")
                    return

            id_object = vim.vslm.ID()
            id_object.id = list_vdiskid_ds[0]

            # DataStores view stored in vms object
            dss = get_obj(si, si.content.rootFolder, [vim.Datastore])

            # Filtering
            filter_propertys = ["name"]
            filter_value = list_vdiskid_ds[1]
            obj_type = vim.Datastore
            ds_obj = Filter_Obj(si, dss, filter_propertys,
                                filter_value, obj_type)

            # snapshot taken with the vstorageobjectmanager api
            snapshot_task = content.vStorageObjectManager.VStorageObjectCreateSnapshot_Task(id_object, ds_obj,
                                                                                            description)

            # calling wait_for_task module to monitor the task
            tasks.wait_for_tasks(si, [snapshot_task])

            print("##Snapshot taken successfully on disk %s. Task id : %s" %
                  (n, snapshot_task))

        except Exception as e:
            print("##Exception in taking snapshot %s " % (e))

 
def view_snapshot(si, content, vm_obj,  dn, disk_prefix_label='Hard disk '):
    """view the VM and FCD disk level snapshots"""

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
                    raise RuntimeError(
                        "##Virtual {} could not be found".format(disk_label))

                list_vdiskid_ds = find_disk(content, vm_obj,
                                            disk_label)  # Returned values are vdiskid, datastore, controllerKey and unitNumber
                
                # If not an FCD then do not perform the detach operation and abort the program as it's difficult to attach it later
                if list_vdiskid_ds[0] is None:
                    print("{} is Not an FCD disk".format(disk_label))
                
                else:

                    id_object = vim.vslm.ID()
                    id_object.id = list_vdiskid_ds[0]  # virtual disk identifier

                    # DataStores view stored in vms object
                    dss = get_obj(si, si.content.rootFolder, [vim.Datastore])

                    # Filtering
                    filter_propertys = ["name"]
                    filter_value = list_vdiskid_ds[1]
                    obj_type = vim.Datastore
                    ds_obj = Filter_Obj(
                        si, dss, filter_propertys, filter_value, obj_type)

                    snapshot = content.vStorageObjectManager.RetrieveSnapshotInfo(
                        id_object, ds_obj)

                    print("\n\n##The snapshots of FCD disk#%s are :" % n)
                    count = 1
                    for sn in snapshot.snapshots:
                        print(
                            "\t\t#%s -> description : %s, creation Time : %s , snapshot identifier : %s " % (
                                count, sn.description, sn.createTime, sn.id.id))
                        count = count + 1

            except Exception as e:
                print("##Exception in viewing the snapshot : %s " % (e))
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

                # if FCD
                if id_object:
                    snapshot = content.vStorageObjectManager.RetrieveSnapshotInfo(
                        id_object, ds_obj)

                    print("\n##The snapshots of FCD %s are :" %
                          virtual_disk_device.deviceInfo.label)
                    count = 1
                    for sn in snapshot.snapshots:
                        print("\t\t#%s -> description : %s, creation Time : %s , snapshot identifier : %s " %
                              (count, sn.description, sn.createTime, sn.id.id))
                        count = count + 1
                else:
                    print("\n{} is not FCD".format(
                        virtual_disk_device.deviceInfo.label))

        except Exception as e:
            print("##Exception in viewing the snapshot : %s " % (e))


def view_vDisk_Snapshot(si, content, id, ds):
    """ This module helps in viewing Snapshot with vDiskId instead of referencing VM as FCD is independent entity"""

    # DataStores view stored in vms object
    dss = get_obj(si, si.content.rootFolder, [vim.Datastore])

    # Filtering
    filter_propertys = ["name"]
    filter_value = ds
    obj_type = vim.Datastore
    ds_obj = Filter_Obj(si, dss, filter_propertys, filter_value, obj_type)

    vDiskId_object = vim.vslm.ID()
    vDiskId_object.id = id

    snapshot = content.vStorageObjectManager.RetrieveSnapshotInfo(
        vDiskId_object, ds_obj)

    print("<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Snapshots at FCD level >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")

    print("\n##The snapshots of FCD disk with id %s are :" % id)
    count = 1
    for sn in snapshot.snapshots:
        print("\t\t#%s -> description : %s, creation Time : %s , snapshot identifier : %s " %
              (count, sn.description, sn.createTime, sn.id.id))
        count = count + 1
    print("\n")


def delete_snapshot(si, content, vm_obj,  dn, snid, disk_prefix_label='Hard disk '):
    """ Delete the FCD disk level snapshot """

    try:

        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError(
                "##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj,
                                    disk_label)  # return is list with two values one is vdisk id and other is datastore name

        # if virtual disk is not found
        if list_vdiskid_ds[0] is None:
            raise RuntimeError(
                "##Virtual {} could not be found".format(disk_label))

        id_object1 = vim.vslm.ID()
        id_object1.id = list_vdiskid_ds[0]

        id_object2 = vim.vslm.ID()
        id_object2.id = snid

        # DataStores view stored in vms object
        dss = get_obj(si, si.content.rootFolder, [vim.Datastore])

        # Filtering
        filter_propertys = ["name"]
        filter_value = list_vdiskid_ds[1]
        obj_type = vim.Datastore
        ds_obj = Filter_Obj(si, dss, filter_propertys, filter_value, obj_type)

        snapshot_task = content.vStorageObjectManager.DeleteSnapshot_Task(
            id_object1, ds_obj, id_object2)
        tasks.wait_for_tasks(si, [snapshot_task])

        print("##Deleted the snapshot with snapshot id  %s. Task id : %s " %
              (snid, snapshot_task))

    except Exception as e:
        print("##Exception in deleting the snapshot is : %s " % (e))


def revert_snapshot(si, content, vm_obj,  dn, snid, disk_prefix_label='Hard disk '):
    """Revert to the FCD disk level snapshot """

    try:

        disk_label = disk_prefix_label + str(dn)
        virtual_disk_device = None

        # find the disk device
        for dev in vm_obj.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
                virtual_disk_device = dev

        # if virtual disk is not found
        if not virtual_disk_device:
            raise RuntimeError(
                "##Virtual {} could not be found".format(disk_label))

        list_vdiskid_ds = find_disk(content, vm_obj,
                                    disk_label)  # return is list with two values one is vdisk id and other is datastore name

        # if virtual disk is not found
        if list_vdiskid_ds[0] is None:
            raise RuntimeError(
                "###{} is not FCD. Hence, not performing the revert operation!".format(disk_label))

        id_object1 = vim.vslm.ID()
        id_object1.id = list_vdiskid_ds[0]

        id_object2 = vim.vslm.ID()
        id_object2.id = snid

        # DataStores view stored in vms object
        dss = get_obj(si, si.content.rootFolder, [vim.Datastore])

        # Filtering
        filter_propertys = ["name"]
        filter_value = list_vdiskid_ds[1]
        obj_type = vim.Datastore
        ds_obj = Filter_Obj(si, dss, filter_propertys, filter_value, obj_type)

        # Detaching the disk before revert as that's the design of FCD 6.7 atleast!!!!
        Detach_vmdk(si, content, vm_obj, dn)

        # I am reverting with the FCD api
        snapshot_task = content.vStorageObjectManager.RevertVStorageObject_Task(
            id_object1, ds_obj, id_object2)
        tasks.wait_for_tasks(si, [snapshot_task])
        print("##Reverted to the snapshot with snapshot id  %s. Task id : %s " % (
            snid, snapshot_task))

        # Look, I am immediately attaching it back !!!
        Attach_vmdk(si, content, vm_obj,
                    list_vdiskid_ds[0], list_vdiskid_ds[1], list_vdiskid_ds[2], list_vdiskid_ds[3])

    except Exception as e:
        print("##Exception in Reverting to the snapshot is : %s " % (e))

        # I am attaching disk that is detached back, though snapshot revert process raised an exception
        # list_vdiskid_ds[0] ,list_vdiskid_ds[1],list_vdiskid_ds[2] , list_vdiskid_ds[3] => vdid, ds, controllerKey, unitNumber
        Attach_vmdk(si, content, vm_obj,
                    list_vdiskid_ds[0], list_vdiskid_ds[1], list_vdiskid_ds[2], list_vdiskid_ds[3])
        print("##The disk %s is attached back." % (disk_label))
        print("##Could not revert to snapshot in time instant because of %s exception, but attached the disk back again which was detached for revert operation" % (e))


def get_disk_number(vm_obj):
    """
    Get total disk numbers
    """
    # disk count
    count = 0

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk):
            count = count + 1

    return count


def detach_attach(disk_number_before_modify, vc_name, si, content, vm_obj, disk_number, disk_prefix_label='Hard disk '):
    """
    Detach and attach the disk here to reset the disk label
    """

    disk_label = disk_prefix_label + str(disk_number)
    virtual_disk_device = None

    # Now before detach & attach make sure it's already promoted
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

            # if not FCD, make it FCD
            if virtual_disk_device.vDiskId is None:
                print(
                    "\n##The Hard disk {} should be promoted to FCD before Taking FCD level Snapshot".format(disk_number_before_modify))
                dcname = find_DCname(vm_obj)
                fcd_op.mkfcd(vc_name, si, dcname, content, vm_obj, disk_number)

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

    # if virtual disk is not found
    if not virtual_disk_device:
        raise RuntimeError(
            "##Virtual {} could not be found".format(disk_label))

    list_vdiskid_ds = find_disk(content, vm_obj,
                                disk_label)  # return is list with two values one is vdisk id and other is datastore name

    # if virtual disk is not found
    if list_vdiskid_ds[0] is None:
        raise RuntimeError(
            "###Hard disk {} is not an FCD. Hence, not performing the detach and attach operation".format(disk_number_before_modify))

    # DataStores view stored in vms object
    dss = get_obj(si, si.content.rootFolder, [vim.Datastore])

    # Filtering
    filter_propertys = ["name"]
    filter_value = list_vdiskid_ds[1]
    obj_type = vim.Datastore
    ds_obj = Filter_Obj(si, dss, filter_propertys, filter_value, obj_type)

    # Detaching the disk before revert as that's the design of FCD 6.7 atleast!!!!
    Detach_vmdk(si, content, vm_obj, disk_number)

    # Look, I am immediately attaching it back !!!
    Attach_vmdk(si, content, vm_obj,
                list_vdiskid_ds[0], list_vdiskid_ds[1], list_vdiskid_ds[2], list_vdiskid_ds[3])


def main():
    args = get_args()

    si = SmartConnectNoSSL(host=args.host,
                           user=args.user,
                           pwd=args.password,
                           port=int(args.port))
    atexit.register(Disconnect, si)

    content = si.RetrieveContent()

    if args.vmname:

        # VMs view stored in vms object
        vms = get_obj(si, si.content.rootFolder, [vim.VirtualMachine])

        # Filtering
        filter_propertys = ["name"]
        filter_value = args.vmname
        obj_type = vim.VirtualMachine
        vm_obj = Filter_Obj(si, vms, filter_propertys, filter_value, obj_type)

        if vm_obj:
            print("###Found VM %s\n" % args.vmname)

            if args.operation == 'create':
                hw_version = (vm_obj.config.version).split('-')[1]
                if int(hw_version) >= 13:
                    if args.description == '':
                        print("###The snapshot needs description")
                    else:
                        if args.disk_number:
                            create_snapshot(
                                args.host, si, content, vm_obj, args.disk_number, args.description)
                        else:
                            print(
                                "\n###Please specify the disk number as an argument\n")
                else:
                    print(
                        "###Taking snapshot on VM whoes hardware version is less than VMX-13 does not allow revert operation. Hence, better not to go with FCD level snapshot  here.")

            if args.operation == 'view':
                view_snapshot(si, content, vm_obj, args.disk_number)
                print("\n\n")

            if args.operation == 'delete':
                if args.disk_number:
                    delete_snapshot(si, content, vm_obj,
                                    args.disk_number, args.snid)
                else:
                    print("\n###Please specify the disk number as an argument\n")

            if args.operation == 'revert':
                if args.disk_number:

                    # Power off the VM as it's not in-memory snapshot before revert
                    if vm_obj.runtime.powerState != "poweredOff":
                        try:
                            print("###Shutting dowm VM {}".format(args.vmname))
                            vm_shutdown_task = vm_obj.ShutdownGuest()
                            tasks.wait_for_tasks(si, [vm_shutdown_task])
                        except vim.fault.ToolsUnavailable as ex:
                            print(ex.msg)
                            print("###Powering VM {} off".format(args.vmname))
                            vm_poweroff_task = vm_obj.PowerOffVM_Task()
                            tasks.wait_for_tasks(si, [vm_poweroff_task])

                    revert_snapshot(si, content, vm_obj,
                                    args.disk_number, args.snid)

                    # Try to perform the post revert operation to detach and attach other disks which are down the line to retain the same disk label names
                    # For example, if I have 5 disks and I am reverting snapshot on disk#3 then I will now attach and detach disk#4, disk#5 too

                    # Get the total number of disks
                    total_disks = get_disk_number(vm_obj)

                    for count in range((int(args.disk_number) + 1), (total_disks + 1)):
                        # workaround for disk label renaming. Detach and attach disk is done over here.
                        # Make sure that every disk is promoted to FCD!!!
                        # Disk name changes every time. Hence, using same disk number to have circular list arrangement!!!

                        detach_attach(count, args.host, si, content,
                                      vm_obj, args.disk_number)
                else:
                    print("\nPlease specify the disk number as an argument\n")

        else:
            print("###VM {} not found\n".format(args.vmname))
    else:
        if args.operation == 'view':
            if args.virtualDiskId and args.dataStore:
                view_vDisk_Snapshot(
                    si, content, args.virtualDiskId, args.dataStore)
            else:
                print("Please provide vDisk Id, DataStore or VM name")
        else:
            print("Please provide the VM name\n")


if __name__ == "__main__":
    main()
