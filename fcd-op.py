#!/usr/bin/env python

#################################################################################################################################
#
#
#pyvmomi script to make disk as FCD
#
#
#################################################################################################################################

from __future__ import print_function

import atexit
import argparse
import getpass
from tools import cli, tasks
from pyVmomi import vim, vmodl
from pyVim.connect import SmartConnectNoSSL, Disconnect
import pdb

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

    parser.add_argument('-vm', '--vmname', required=True,
                        help='Name of the VirtualMachine you want to change.')

    parser.add_argument('-d', '--diskNumber', required=False,
                        help='Disk number to promote to FCD. Can be comma separated values like -d 1,2,3')

    parser.add_argument('-op', '--operation', required= True,
                        choices = ['view', 'promote'],
                        help='Operation to perform')

    args = parser.parse_args()

    if not args.password:
        args.password = getpass.getpass(
            prompt='Enter password for host %s and user %s: ' %
                   (args.host, args.user))
    return args


def create_filter_spec(pc, vms, props):
    """Creates the filter specification"""

    objSpecs = []
    for vm in vms:
        objSpec = vmodl.query.PropertyCollector.ObjectSpec(obj=vm)
        objSpecs.append(objSpec)
    filterSpec = vmodl.query.PropertyCollector.FilterSpec()
    filterSpec.objectSet = objSpecs
    propSet = vmodl.query.PropertyCollector.PropertySpec(all=False)
    propSet.type = vim.VirtualMachine
    for  prop in props:
        (propSet.pathSet).append(prop)
    filterSpec.propSet = [propSet]
    return filterSpec


def filter_results(result, value):
    """Filter the result for the value"""

    for vm in result:
        if value in vm.propSet[0].val:
            return vm.obj
    return None


def Filter_VM(ServiceInstance, vms, filter_propertys, filter_value):
    """ Fileter the VM with it's name"""

    pc = ServiceInstance.content.propertyCollector
    filter_spec = create_filter_spec(pc, vms, filter_propertys)
    options = vmodl.query.PropertyCollector.RetrieveOptions()
    result = pc.RetrieveProperties([filter_spec])
    vm_obj = filter_results(result, filter_value)
    return vm_obj


def get_obj(ServiceInstance, root, vim_type):
    """Create container view and search for object in it"""
    
    container = ServiceInstance.content.viewManager.CreateContainerView(root, vim_type,
                                                                        True)
    view = container.view
    container.Destroy()
    return view


def build_paramters(si, ds, vm, vmdk_file, vc_name, dc_name):
    """Builds the parameter to pass in the registerdisk API"""

    l = vmdk_file.split("/")
    path_parameter = "https://" + vc_name + "/folder/" + vm + "/" + l[len(l) - 1] + "?dcPath=" + dc_name + "&dsName=" + ds
    return path_parameter


def Annotate_VM(si, vm_obj, note):
    """Annotate the VM with the note"""

    #keeping last annotation as a buffer
    previous_annotation = vm_obj.summary.config.annotation
    
    #setting annotation
    spec = vim.vm.ConfigSpec()
    spec.annotation = previous_annotation + note
    task = vm_obj.ReconfigVM_Task(spec)
    tasks.wait_for_tasks(si, [task])
        

def mkfcd(vc_name, si, dc_name, content, vm_obj, disk_number, disk_prefix_label='Hard disk '):
    """Module to promote the virtual disk as FCD"""

    disk_label = disk_prefix_label + str(disk_number)
    virtual_disk_device = None

    # find the disk device
    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

    # if virtual disk is not found
    if not virtual_disk_device:
        print("###Virtual {} could not be found".format(disk_label))
        return False

    # checkigng the disk details
    if hasattr(virtual_disk_device.backing, 'fileName'):
        datastore = virtual_disk_device.backing.datastore
        if datastore:
            summary = {'capacity': datastore.summary.capacity,
                       'freeSpace': datastore.summary.freeSpace,
                       'file system': datastore.summary.type,
                       'url': datastore.summary.url}
            for key, val in summary.items():
                if key == 'url':
                    path_to_disk = val

        path_to_disk += virtual_disk_device.backing.fileName

        parameter_for_fcd_disk = build_paramters(si, datastore.name, vm_obj.name, virtual_disk_device.backing.fileName, vc_name, dc_name)

        try:
             #Invoking the API to register the existing disk as First Class
            vstorage = content.vStorageObjectManager.RegisterDisk(parameter_for_fcd_disk)
        except Exception as exception:
            print("###Could not be promoted to FCD.")
            print("Exception : {}".format(exception))

        annotation_note = "FCD Disk "+ str(disk_number) + " id : " + str(vstorage.config.id.id) + "\n"
        Annotate_VM(si, vm_obj, annotation_note) #To append the annotation note to the VM annotation

    return True


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


def get_fcd_ids(vm_obj, disk_number, disk_prefix_label='Hard disk '):
    """
    Get the FCD disk object IDs
    """

    disk_label = disk_prefix_label + str(disk_number)
    virtual_disk_device = None

    for dev in vm_obj.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk) and dev.deviceInfo.label == disk_label:
            virtual_disk_device = dev

            # if not FCD, make it FCD
            if virtual_disk_device.vDiskId is None:
                print("    {} is not a FCD disk".format(disk_label))
            else:
                print("    {}\t==>\t{}".format(disk_label, virtual_disk_device.vDiskId.id))


def main():
    
    args = get_args()
    si = SmartConnectNoSSL(host=args.host,
                           user=args.user,
                           pwd=args.password,
                           port=int(args.port))
    atexit.register(Disconnect, si)

    content = si.RetrieveContent()

    vms = get_obj(si, si.content.rootFolder, [vim.VirtualMachine]) #VMs view stored in vms object
    
    #Filtering
    filter_propertys = ["name"]
    filter_value = args.vmname
    vm_obj = Filter_VM(si, vms, filter_propertys, filter_value)
    
    if vm_obj is None:
        raise SystemExit("Unable to locate VirtualMachine.")

    else:

        if args.operation == 'promote':

            if args.diskNumber is None:
                raise SystemExit("Please specify the disk number using --diskNumber <diknumbers> as an argument")
            
            #find the Name of VMs DataCenter
            TempParent = vm_obj
            dc = vim.Datacenter
            while True:
                if ( type( TempParent.parent ) != dc ):
                    TempParent = TempParent.parent
                else:
                    break
            
            dcname = TempParent.parent.name

            disk_numbers = args.diskNumber.split(',')

            for n in disk_numbers:
                try:
                    fcd_task = mkfcd(args.host, si, dcname, content, vm_obj, n)

                    if fcd_task is True:
                        print("###The Hard Disk %s is promoted to FCD"% n)
                    else:
                        print("###vDisk %s is not promoted to FCD"% n)
                except Exception as e:
                    print("###Exception in making disk as FCD %s " % (e))
        
        elif args.operation == "view":
            total_disks = get_disk_number(vm_obj)
            if int(total_disks) != 0:
                print("### Disk Label\t==>\tvDiskId")
                for disk_number in range( 1,  int(total_disks) + 1  ):
                    get_fcd_ids(vm_obj, disk_number)
            else:
                print("###The VM does not contain any disk attached")
        else:
            print("###Plase specify the operation.")

            
if __name__ == "__main__":
    main()
