"""
lambda_database.py
------------------
AWS Lambda handler for the Database Agent.

Deploy as: bedrock-database-agent  (AGENT_KEY=database)
"""

import json
import logging
import os
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

event_ref: dict = {}


def get_params(event: dict) -> dict:
    return {p["name"]: p["value"] for p in event.get("parameters", [])}


def ok(body: dict) -> dict:
    return {
        "actionGroup": event_ref["actionGroup"],
        "function":    event_ref["function"],
        "functionResponse": {
            "responseBody": {
                "TEXT": {"body": json.dumps(body, default=str)}
            }
        },
    }


def err(message: str) -> dict:
    return ok({"status": "error", "message": message})


def handle_database(function_name: str, params: dict) -> dict:
    rds = boto3.client("rds", region_name=AWS_REGION)
    dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)

    if function_name == "create_rds_instance":
        kwargs = {
            "DBInstanceIdentifier": params["db_identifier"],
            "Engine": params["engine"],
            "DBInstanceClass": params["instance_class"],
            "AllocatedStorage": int(params["allocated_storage"]),
            "MasterUsername": params["master_username"],
            "MasterUserPassword": params["master_password"],
        }
        if "vpc_security_group_ids" in params:
            kwargs["VpcSecurityGroupIds"] = [s.strip() for s in params["vpc_security_group_ids"].split(",")]
        if "multi_az" in params:
            multi_az_val = params["multi_az"]
            kwargs["MultiAZ"] = multi_az_val.lower() == "true" if isinstance(multi_az_val, str) else bool(multi_az_val)
            
        try:
            resp = rds.create_db_instance(**kwargs)
            return ok({"status": "creating", "db_instance_identifier": resp["DBInstance"]["DBInstanceIdentifier"]})
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_dynamodb_table":
        billing = params.get("billing_mode", "PAY_PER_REQUEST")
        key_type = params["key_type"]
        kwargs = {
            "TableName": params["table_name"],
            "KeySchema": [{"AttributeName": params["partition_key"], "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": params["partition_key"], "AttributeType": key_type}],
            "BillingMode": billing,
        }
        if billing == "PROVISIONED":
            kwargs["ProvisionedThroughput"] = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}
            
        try:
            resp = dynamodb.create_table(**kwargs)
            return ok({"status": "creating", "table_name": resp["TableDescription"]["TableName"], "table_arn": resp["TableDescription"]["TableArn"]})
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown Database function: {function_name}")


HANDLER_MAP = {"database": handle_database}


def lambda_handler(event: dict, context) -> dict:
    global event_ref
    event_ref = event
    log.info("Event: %s", json.dumps(event, default=str))

    agent_key = os.environ.get("AGENT_KEY", "database")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key!r}")

    function_name = event.get("function", "")
    params = get_params(event)
    return handler(function_name, params)
