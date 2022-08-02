#!/usr/bin/env python3

import boto3
from botocore.exceptions import ClientError
import json
import logging
import string
import random
import os
import re

## https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets-lambda-function-overview.html#rotation-explanation-of-steps
## Step can be:
##  - createSecret: the Lambda function generates a new version of the secret. 
##                  Secrets Manager then labels the new version of the secret 
##                  with the staging label AWSPENDING to mark it as the 
##                  in-process version of the secret.
##  - setSecret...: rotation function retrieves the version of the secret 
##                  labeled AWSPENDING from Secrets Manager
##  - testSecret..: the Lambda function verifies the AWSPENDING version of the secret
##  - finishSecret: move the label AWSCURRENT from the current version 
##                  to this new version of the secret so your clients start using it.

##
## There are three steps to care about: create, set, and finish
##   - createSecret: writes the new AWSPENDING secret
##   - setSecret...: will update alb
##   - finishSecret: changes AWSPENDING to AWSCURRENT 
##


##
## ℹ️ By default, the LOG LEVEL will be INFO.
## ⚠️ Setting the LOG LEVEL to DEBUG will print secrets to the CloudWatch Logs.
## 🛑 DO NOT SET LOG LEVEL TO DEBUG UNLESS YOU KNOW WHAT YOU ARE DOING.
##
logger   = logging.getLogger()
loglevel = os.environ.get('LOGLEVEL', 'INFO')
if (loglevel.lower() == "debug"):
  logger.setLevel(logging.DEBUG)
else:
  logger.setLevel(logging.INFO)


secretsmanager = boto3.client('secretsmanager')
elbv2          = boto3.client('elbv2')

######################################################
##
## Get Secret utility function
## - This function is used to get the current secret value
##
######################################################

def get_secret(secret_id, stage="AWSCURRENT", token=None):
  ##
  ## https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/secretsmanager.html#SecretsManager.Client.get_secret_value
  ##
  logger.debug(f"Getting [{stage}] Secret: [{secret_id}]")
  response=secretsmanager.get_secret_value(SecretId=secret_id,
                                          VersionStage=stage)
  secret_value = response['SecretString']
  logger.debug("Retrieved Secret: [%s]", secret_value)
  return secret_value

######################################################
##
## Generate API Key
## - Generates a random-string API key.
##
######################################################
def key_generator(size=16):
  chars = string.ascii_uppercase + string.ascii_lowercase + string.digits
  return ''.join(random.choice(chars) for _ in range(size))



######################################################
##
## Update Custom API Key Header in alb
## - Gets a list of alb Distributions
## - Gets the *ALIASES* environment variable
## - ALIASES is a list of domain names seperated by a comma.
## - If a alb Distribution contains an *alias*
##   in the *ALIASES* environment variable, then
##   set the API Key header.
##
######################################################
def set_api_key_in_alb(secret_value=None):
  if secret_value is None:
    return 1




  ##
  ## UPDATE HERE DPH
  ##
  logger.debug(f"Setting API Key in ALB: {secret_value}")
  ##
  ## Get ALBs
  ## Get Listeners
  ## Get Rules
  ## Set AWS API Key
  ##
  response = elbv2.describe_load_balancers()
  if "LoadBalancers" in response:
    for alb in response['LoadBalancers']:
      alb_name = alb['LoadBalancerName']
      alb_arn  = alb['LoadBalancerArn']
      ##
      ## Get Environment Variables
      ##
      ## If ALBNAME not set, rotate ALL ALBs
      ##
      alb_to_rotate   = os.environ.get("ALBNAME", "")
      rotate_this_alb = False
      if (alb_to_rotate == ""):
        rotate_this_alb = True


      ##
      ## If ALB Name matches the ALBNAME env var, rotate.
      ##
      if (alb_to_rotate in alb_name):
        rotate_this_alb = True

        ##
        ## If ALB Name matched, update headers
        ##
      if rotate_this_alb:
        ##
        ## Download the current config
        ##
        listener_response = elbv2.describe_listeners(
          LoadBalancerArn=alb_arn
        )
        for listener in listener_response["Listeners"]:
          logger.debug(f"Listener: {json.dumps(listener)}")
          listener_arn = listener["ListenerArn"]
          rule_response = elbv2.describe_rules(
            ListenerArn = listener_arn
          )
          logger.debug(f"rule response: {json.dumps(rule_response)}")
          for rule in rule_response["Rules"]:
            logger.debug(f"Rule: {json.dumps(rule)}")
            if rule["IsDefault"]:
              logger.debug("Default Rule. Can not modify default rule. Skipping.")
            else:
              logger.debug("Updating Rule.")
              i = 0
              new_conditions = rule["Conditions"]
              for condition in new_conditions:
                if ("Field" in condition
                    and condition["Field"] == "http-header"
                    and "HttpHeaderName" in condition["HttpHeaderConfig"]
                    and condition["HttpHeaderConfig"]["HttpHeaderName"] ==
                    "X-AWS-API-KEY"):
                  logger.debug(
                      f'Current Values: {json.dumps(new_conditions[i]["HttpHeaderConfig"]["Values"])}'
                  )
                  new_conditions[i]["HttpHeaderConfig"]["Values"] = [secret_value]
                  logger.debug(
                      f'New Values: {json.dumps(new_conditions[i]["HttpHeaderConfig"]["Values"])}'
                  )
                i = i + 1
              modified_rule_response = elbv2.modify_rule(RuleArn=rule["RuleArn"],Conditions=new_conditions,Actions=rule["Actions"])
              logger.debug(f"Modification Response: {json.dumps(modified_rule_response)}")

  return 0

######################################################
##
## step-specific functions
##
######################################################

##
## - The Lambda function generates a new version of the secret. 
## - Secrets Manager then labels the new version of the secret 
##   with the staging label AWSPENDING to mark it as the 
##   in-process version of the secret.
## - This method first checks for the existence of a secret for the passed in token. 
## - If one does not exist, it will generate a
##   new secret and put it with the passed in token.
##
def create_secret(secret_id, token=None):
  logger.debug("CREATING SECRET")

  old_secret_value      = get_secret(secret_id, stage="AWSCURRENT", token=token)
  logger.debug(f"* old_secret_value 1: {str(type(old_secret_value))}")
  logger.debug(f"* old_secret_value 1: {str(old_secret_value)}")
  old_secret_value_json = json.loads(old_secret_value)
  logger.debug(f"* old_secret_value_json 1: {str(type(old_secret_value_json))}")
  logger.debug(f"* old secret: {old_secret_value}")

  # Now try to get the secret version, if that fails, put a new secret
  try:
    pending_secret_value = secretsmanager.get_secret_value(SecretId=secret_id, VersionId=token, VersionStage="AWSPENDING")
    logger.info(f"createSecret: Successfully retrieved secret for {secret_id}.")
    logger.debug(f"* pending secret: {pending_secret_value}")

  except secretsmanager.exceptions.ResourceNotFoundException:

    logger.debug(f"* old_secret_value..... 2: {str(type(old_secret_value))}")
    logger.debug(f"* old_secret_value_json 2: {str(type(old_secret_value_json))}")
    logger.debug(f"* old_secret_value_json 2: {str(old_secret_value_json)}")

    old_secret_value_json=json.loads(old_secret_value)
    logger.debug(f"* old_secret_value..... 3: {str(type(old_secret_value))}")
    logger.debug(f"* old_secret_value_json 3: {str(type(old_secret_value_json))}")
    logger.debug(f"* old_secret_value_json 3: {str(old_secret_value_json)}")

    new_secret_value = {
      "key1": old_secret_value_json["key2"],
      "key2": old_secret_value_json["key3"],
      "key3": key_generator(),
    }
    logger.debug("* new secret: %s" % json.dumps(new_secret_value).replace("\\", ""))

    ##
    ## https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/secretsmanager.html#SecretsManager.Client.put_secret_value
    ##
    secretsmanager.put_secret_value(SecretId=secret_id, ClientRequestToken=token, SecretString=json.dumps(new_secret_value).replace("\\", ""), VersionStages=['AWSPENDING'])
    logger.info(
        f"createSecret: Successfully put secret for secret_id {secret_id} and version {token}."
    )

  return 0

##
## - Rotation function retrieves the version of the secret 
##   labeled AWSPENDING from Secrets Manager
## - This method should set the AWSPENDING secret in the service that 
##   the secret belongs to.
## - This method will take the value of the AWSPENDING secret and set the 
##   API Key in alb
##
def set_secret(secret_id, token=None):
  logger.debug("SETTING SECRET")
  secret_value = get_secret(secret_id, stage="AWSPENDING",token=token)
  logger.debug(f"* secret_value.....: {secret_value}")
  logger.debug(f"* secret_value.....: {str(type(secret_value))}")
  secret_value_json = json.loads(secret_value.replace("'", '"').replace("\\", ""))
  logger.debug(f"* secret_value_json: {repr(secret_value_json)}")
  logger.debug(f"* secret_value_json: {str(type(secret_value_json))}")
  logger.debug(f'setting api key in alb: {secret_value_json["key3"]}')
  set_api_key_in_alb(secret_value_json["key3"])
  return 0

##
## - The Lambda function verifies the AWSPENDING version of the secret
## - This method should validate that the AWSPENDING secret works in 
##   the service that the secret belongs to.
##
def test_secret(secret_id, token=None):
  logger.debug("TESTING SECRET")
  secret_value_current = get_secret(secret_id, stage="AWSCURRENT",token=token)
  secret_value_pending = get_secret(secret_id, stage="AWSPENDING",token=token)
  logger.debug(f"* current secret: {secret_value_current}")
  logger.debug(f"* pending secret: {secret_value_pending}")
  return 0

##
## - Move the label AWSCURRENT from the current version 
##   to this new version of the secret so your clients start using it.
## - This method finalizes the rotation process by marking the secret 
##   version passed in as the AWSCURRENT secret.
##
def finish_secret(secret_id, token=None):
  logger.debug("FINISHING SECRET")
  secret_value = get_secret(secret_id, stage="AWSPENDING",token=token)
  logger.debug(f"* pending secret: {secret_value}")

  # Describe the secret to get the current version
  metadata = secretsmanager.describe_secret(SecretId=secret_id)
  current_version = None
  for version in metadata["VersionIdsToStages"]:
    if "AWSCURRENT" in metadata["VersionIdsToStages"][version]:
      if version == token:
              # The correct version is already marked as current, return
        logger.info(
            f"finishSecret: Version {version} already marked as AWSCURRENT for {secret_id}"
        )
        return
      current_version = version
      break

  # Finalize by staging the secret version current
  secretsmanager.update_secret_version_stage(SecretId=secret_id, VersionStage="AWSCURRENT", MoveToVersionId=token, RemoveFromVersionId=current_version)

  return 0


######################################################
##
## Main function of the Lambda
##
######################################################

def lambda_handler(event, context):
  logger.debug(event)

  step      = event["Step"]
  secret_id = event["SecretId"]
  token = event['ClientRequestToken'] if 'ClientRequestToken' in event else ""
  ##
  ## Ensure the version is staged correctly
  ##
  metadata = secretsmanager.describe_secret(SecretId=secret_id)
  if not metadata['RotationEnabled']:
    logger.error(f"Secret {secret_id} is not enabled for rotation")
    raise ValueError(f"Secret {secret_id} is not enabled for rotation")
  versions = metadata['VersionIdsToStages']
  if token not in versions:
    logger.error(
        f"Secret version {token} has no stage for rotation of secret {secret_id}."
    )
    raise ValueError(
        f"Secret version {token} has no stage for rotation of secret {secret_id}."
    )
  if "AWSCURRENT" in versions[token]:
    logger.info(
        f"Secret version {token} already set as AWSCURRENT for secret {secret_id}."
    )
    return
  elif "AWSPENDING" not in versions[token]:
    logger.error(
        f"Secret version {token} not set as AWSPENDING for rotation of secret {secret_id}."
    )
    raise ValueError(
        f"Secret version {token} not set as AWSPENDING for rotation of secret {secret_id}."
    )




  logger.debug(f"Step.....: [{step}] ({step.lower()})")
  logger.debug(f"Secret ID: [{secret_id}]")
  logger.debug(f"Token....: [{token}]")
    ##
    ## Steps are lowercase+Secret:
    ##   - createSecret
    ##   - setSecret
    ##   - testSecret
    ##   - finishSecret
    ##
  if (step.lower() == "createsecret"):
    logger.debug("CreateSecret step")
    create_secret(secret_id, token)
  elif (step.lower() == "setsecret"):
    logger.debug("SetSecret step")
    set_secret(secret_id, token)
  elif (step.lower() == "testsecret"):
    logger.debug("TestSecret step")
    test_secret(secret_id, token)
  elif (step.lower() == "finishsecret"):
    logger.debug("FinishSecret step")
    finish_secret(secret_id, token)
  else:
    logger.debug(f"unknown step: {step}")

  return {
      'statusCode': 200,
      'body': "done."
  }






