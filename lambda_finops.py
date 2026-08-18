"""
lambda_finops.py
----------------
AWS Lambda handler for the FinOps Agent.

Deploy as: bedrock-finops-agent  (AGENT_KEY=finops)
"""

import json
import logging
import os
from datetime import datetime, timedelta
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


def handle_finops(function_name: str, params: dict) -> dict:
    if function_name == "get_cost_forecast":
        ce = boto3.client("ce", region_name="us-east-1")
        today = datetime.utcnow()
        start_date = today.strftime('%Y-%m-01')
        end_date = (today.replace(day=28) + timedelta(days=4)).replace(day=1).strftime('%Y-%m-%d')
        
        try:
            resp = ce.get_cost_forecast(
                TimePeriod={'Start': start_date, 'End': end_date},
                Metric='UNBLENDED_COST',
                Granularity='MONTHLY'
            )
            return ok({"forecast": resp.get("Total", {})})
        except ClientError as e:
            return err(str(e))

    elif function_name == "get_instance_price":
        pricing = boto3.client("pricing", region_name="us-east-1")
        try:
            resp = pricing.get_products(
                ServiceCode='AmazonEC2',
                Filters=[
                    {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': params["instance_type"]},
                    {'Type': 'TERM_MATCH', 'Field': 'operatingSystem', 'Value': params.get("os", "Linux")},
                    {'Type': 'TERM_MATCH', 'Field': 'preInstalledSw', 'Value': 'NA'},
                    {'Type': 'TERM_MATCH', 'Field': 'tenancy', 'Value': 'Shared'},
                    {'Type': 'TERM_MATCH', 'Field': 'capacitystatus', 'Value': 'Used'}
                ],
                MaxResults=1
            )
            if not resp.get("PriceList"):
                return err(f"No pricing found for {params['instance_type']}")
            
            price_item = json.loads(resp["PriceList"][0])
            terms = price_item.get("terms", {}).get("OnDemand", {})
            for term_key in terms:
                price_dimensions = terms[term_key].get("priceDimensions", {})
                for dim_key in price_dimensions:
                    price_per_hr = price_dimensions[dim_key].get("pricePerUnit", {}).get("USD")
                    return ok({"instance_type": params["instance_type"], "os": params.get("os", "Linux"), "price_per_hour_usd": price_per_hr})
                    
            return err("Could not parse price from pricing API response")
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown FinOps function: {function_name}")


HANDLER_MAP = {"finops": handle_finops}


def lambda_handler(event: dict, context) -> dict:
    global event_ref
    event_ref = event
    log.info("Event: %s", json.dumps(event, default=str))

    agent_key = os.environ.get("AGENT_KEY", "finops")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key!r}")

    function_name = event.get("function", "")
    params = get_params(event)
    return handler(function_name, params)
