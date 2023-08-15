# INITIALIZE AWS SDK BOTO3 CLIENT AND CONTEXT

import boto3
from botocore.config import Config
import logging

# USER SUPPLIED VALUES
aws_region                      = "us-east-1"
instance_id                     = ""
kms_cmk_id                      = "alias/"
target_accounts                 = ['']
volume_id                       = ""
ami_name                        = ""
ami_default_architecture        = "x86_64"

# MAIN FUNCTION GENERATED VALUES
instance_device_name            = ""
instance_snapshots              = []
root_volume_snapshot_id         = ""
snapshot_copies                 = []
ami_block_device_mappings       = []
registered_ami_id               = ""

# LOGGING CONFIGURATION
# create logger
logger = logging.getLogger('ami_creation')
logger.setLevel(logging.INFO)

# create console handler and set level to INFO
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# create formatter
formatter = logging.Formatter('%(asctime)s: %(levelname)s - %(message)s')

# add formatter to console_handler
console_handler.setFormatter(formatter)

# add console_handler to logger
logger.addHandler(console_handler)

# AWS BOTO3 EC2 CLIENT CONFIGURATION
my_config = Config(
    region_name = aws_region,    
)
client = boto3.client('ec2', config=my_config)
snapshot_waiter = client.get_waiter('snapshot_completed')


# GET INSTANCE BLOCK DEVICE MAPPING
def get_instance_data(instance):
    response = client.describe_instances(
        InstanceIds=[
            instance
        ]
    )
    root_device_name = response['Reservations'][0]['Instances'][0].get('RootDeviceName')
    block_device_mappings = response['Reservations'][0]['Instances'][0].get('BlockDeviceMappings')
    root_volume = [ id['Ebs']['VolumeId'] for id in block_device_mappings if id['DeviceName'] in root_device_name]
    return root_volume[0], root_device_name, block_device_mappings

# CREATE SNAPSHOT
def create_instance_snapshot(instance):
    response = client.create_snapshots(
        InstanceSpecification={
            'InstanceId': instance
        },
        TagSpecifications=[
            {
                'ResourceType': 'snapshot',
                'Tags': [
                    {
                        'Key': 'instance_id',
                        'Value': instance
                    }
                ]
            }
        ]
    )
    return [{'SnapshotId': snapshot.get('SnapshotId'), 'VolumeId': snapshot.get('VolumeId')}
            for snapshot in response['Snapshots']]

# GET ROOT SNAPSHOT
def get_root_snapshot(root_volume, snapshots):
    root_snapshot = [ snapshot['SnapshotId'] for snapshot in snapshots if snapshot['VolumeId'] in root_volume ]
    return root_snapshot[0]

def create_volume_snapshot(volume):
    response = client.create_snapshot(
        VolumeId=volume
    )
    return

# TAG SNAPSHOT
def tag_snapshot(snapshot, tags):
    response = client.create_tags(
        Resources=[
            snapshot
        ],
        Tags=tags
    )
    return

# COPY SNAPSHOT
def copy_snapshot(snapshot, kmskey, region, instance):
    response = client.copy_snapshot(
        SourceSnapshotId=snapshot,
        Encrypted=True,
        KmsKeyId=kmskey,
        SourceRegion=region,
        TagSpecifications=[
            {
                'ResourceType': 'snapshot',
                'Tags': [
                    {
                        'Key': 'instance_id',
                        'Value': instance
                    }
                ]
            } 
        ]
    )
    return response['SnapshotId']

# REGISTER AMI
def register_ami(name, architecture, mappings, device_name):
    response = client.register_image(
        Name=name,
        Architecture=architecture,
        BlockDeviceMappings=mappings,
        RootDeviceName=device_name
    )
    return response['ImageId']

# SHARE AMI
def share_ami(ami, accounts):
    response = client.modify_image_attribute(
        ImageId=ami,
        LaunchPermission={
            'Add': [ {'UserId': account} for account in accounts ]
        }
    )
    return response

# Main

def main():

    logger.info('Creating AMI from %s', instance_id)

    # GET ROOT VOLUME
    instance_root_volume, instance_root_device_name, instance_block_device_mapping = get_instance_data(instance_id)

    logger.info('Snapshot creation process started...')

    # CREATE INSTANCE SNAPSHOTS
    instance_snapshots = create_instance_snapshot(instance_id)

    # WAIT UNTIL SNAPSHOTS ARE COMPLETED
    snapshot_waiter.wait(
        SnapshotIds=[id.get('SnapshotId') for id in instance_snapshots]
    )

    logger.info('Snapshot created successfully! The following are the IDs: %s', instance_snapshots)

    # GET ROOT VOLUME SNAPSHOT ID
    root_volume_snapshot_id = get_root_snapshot(instance_root_volume, instance_snapshots)

    logger.info('Snapshot copy process started...')

    # CREATE SNAPSHOT COPIES
    for snapshot in instance_snapshots:
        snapshot_copy_id = copy_snapshot(snapshot['SnapshotId'], kms_cmk_id, aws_region, instance_id)
        # TAG ROOT VOLUME SNAPSHOT COPY AS root_volume SNAPSHOT
        if snapshot['SnapshotId'] in root_volume_snapshot_id:
            tags=[
                {
                    'Key': 'root_volume',
                    'Value': 'yes'
                }
            ]
            tag_snapshot(snapshot['SnapshotId'], tags)
            tag_snapshot(snapshot_copy_id, tags)
            # INSERT ROOT VOLUME SNAPSHOT COPY AS FIRST ITEM IN THE LIST
            snapshot_copies.insert(0, snapshot_copy_id)
        else:
            # APPEND OTHER SNAPSHOT COPIES TO THE LIST
            snapshot_copies.append(snapshot_copy_id)
    
    # WAIT UNTIL SNAPSHOT COPIES ARE COMPLETED
    snapshot_waiter.wait(
        SnapshotIds=snapshot_copies
    )

    logger.info('Snapshot copies created successfully! The following are the IDs: %s', snapshot_copies)

    # DEFINE BLOCK DEVICE MAPPINGS FOR THE AMI
    ami_block_device_mappings = [ { 'DeviceName': device['DeviceName'], 'Ebs': {'SnapshotId': snapshot, 'VolumeType': 'gp3'}} 
                                 for device, snapshot in zip(instance_block_device_mapping, snapshot_copies) ]
    
    logger.info('Registering AMI %s with arch %s on region %s...', ami_name, ami_default_architecture, aws_region)

    # REGISTER AMI
    registered_ami_id = register_ami(ami_name, ami_default_architecture, ami_block_device_mappings, instance_root_device_name)

    logger.info('AMI registered successfully! Now adding permissions for the accounts %s...', target_accounts)

    # SHARE AMI
    share_ami(registered_ami_id, target_accounts)

    logger.info('AMI successfully shared! Go and deploy your new AMI')
    logger.info('Job done!')

    return

main()