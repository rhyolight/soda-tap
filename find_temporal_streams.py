import datetime
import json
import os
import sys
import urlparse

import redis
from termcolor import colored

from sodatap import createCatalog

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
REDIS_URL = os.environ["REDIS_URL"]
REDIS_DB = 0
POOL = None

def isDateString(str):
  try:
    datetime.datetime.strptime(str, DATE_FORMAT)
    return True
  except ValueError:
    return False


def getTemporalFields(point):
  temporalFieldNames = []
  # find the temporal field names
  for key, value in point.iteritems():
    if isinstance(value, basestring) and isDateString(value):
      temporalFieldNames.append(key)
  return temporalFieldNames


def getPrimaryTemporalField(temporalFieldNames):
  if len(temporalFieldNames) == 0:
    return None
  name = temporalFieldNames[0]
  for n in temporalFieldNames:
    if "created" in n.lower() or \
       "open" in n.lower() or \
       "received" in n.lower():
      name = n
  return name


def isNonNumericalNumberString(key):
  blacklist = [
    "zip", "address", "latitude", "longitude", "incidentid",
    "number", "code", "year", "month", "meter_id", "bldgid",
    "parcel_no", "case", "_no", "uniquekey", "district",
    "_id", "_key", "checknum", "_group", "crimeid", "facility",
    "phone", "licensenum", "_status", "fileno", "cnty_cd", "day",
    "extra_multiplier"
  ]
  for word in blacklist:
    if word in key:
      return True
  return False


def getDataType(key, value):
  if isNonNumericalNumberString(key):
    dataType = "str"
  elif isinstance(value, dict):
    if "type" in value.keys() and value["type"] == "Point":
      dataType = "location"
    else:
      dataType = "dict"
  elif isinstance(value, list):
    dataType = "list"
  else:
    try:
      int(value)
      dataType = "int"
    except ValueError:
      try:
        float(value)
        dataType = "float"
      except ValueError:
        if isDateString(value):
          dataType = "date"
        else:
          dataType = "str"
  return dataType


def extractFieldTypesFrom(dataPoint):
  fieldMeta = {}
  for key, val in dataPoint.iteritems():
    fieldMeta[key] = getDataType(key, val)
  return fieldMeta


def storeResource(redisClient, resource, field, fieldTypes):
  id = resource.getId()
  type = "scalar"
  fieldNames = fieldTypes.values()
  if "location" in fieldNames \
          or ("latitude" in fieldNames 
              and "longitude" in fieldNames):
    type = "geospatial"
  redisClient.set(id, json.dumps({
    "type": type,
    "temporalField": field,
    "jsonUrl": resource.getJsonUrl(),
    "fieldTypes": fieldTypes,
    "catalogEntry": resource.json()
  }))
  redisClient.sadd(type, id)


def getFieldRanking(data):
  counts = {}
  for point in data:
    for k, v in point.iteritems():
      if v is None or v == "":
        if k not in counts.keys():
          counts[k] = 1
        else:
          counts[k] += 1
  # print counts



def validateTemporal(name, temporalField, data, fieldTypes):
  # State lottery is pretty useless from what I have seen.
  if "lottery" in name.lower() or "lotto" in name.lower():
    raise ValueError("Lottery stream.")
  
  # Not temporal if there are less than 100 data points.
  if len(data) < 100:
    raise ValueError("Not enough data to analyze.")
  
  # Not temporal if there are no ints or floats or locations involved.
  allTypes = fieldTypes.values()
  if "int" not in allTypes \
      and "float" not in allTypes \
      and "location" not in allTypes:
    raise ValueError("No scalars or locations found.")
  
  # If any points are missing a temporal field, not temporal.
  for point in data:
    if temporalField not in point.keys():
      raise ValueError("Some points are missing temporal field values.")
  
  # If the first and last points have the same date, not temporal.
  firstDate = data[0][temporalField]
  lastDate = data[len(data) - 1][temporalField]
  if firstDate == lastDate:
    raise ValueError("No temporal movement over data.")
  
  # If latest data is old, not temporal.
  today = datetime.datetime.today()
  lastDate = datetime.datetime.strptime(lastDate, DATE_FORMAT)
  monthAgo = today - datetime.timedelta(days=28)
  if lastDate < monthAgo:
    raise ValueError("Data is over a month old.")
  
  # If data is in the future, that ain't right.
  if lastDate > today:
    raise ValueError("Data is in the future!")


def run(offset=0):
  redisUrl = urlparse.urlparse(REDIS_URL)
  redisClient = redis.Redis(
    host=redisUrl.hostname, port=redisUrl.port, 
    db=REDIS_DB, password=redisUrl.password
  )
  stored = redisClient.keys("*")
  count = offset
  catalog = createCatalog(offset=offset)

  for page in catalog:
    for resource in page:
      id = resource.getId()
      
      if id == "6me2-sejv":
        print id
        
      name = resource.getName()
      domain = resource.getMetadata()["domain"]
      
      try:
        if id in stored:
          raise ValueError("Already stored.")
        # Need to get one data point to calculate the primary temporal field.
        try:
          dataPoint = resource.fetchData(limit=100)[0]
        except IndexError:
          raise ValueError("No data!")
        except KeyError as e:
          raise ValueError("Error fetching first data point: " + str(e))
        temporalFieldNames = getTemporalFields(dataPoint)
        primaryTemporalField = getPrimaryTemporalField(temporalFieldNames)
        # Not temporal if there's no temporal field identified.
        if primaryTemporalField is None or primaryTemporalField == "":
          raise ValueError("No temporal field found.")
        fieldTypes = extractFieldTypesFrom(dataPoint)
        # Need to get the rest of the data ordered by the temopral field for
        # further analysis.
        try:
          data = list(reversed(resource.fetchData(
            limit=100, order=primaryTemporalField + " DESC"
          )))
        except TypeError as e:
          raise ValueError("Error fetching sample data: " + str(e))
        # If this is a not temporal stream, the function below will raise a
        # ValueError
        validateTemporal(name, primaryTemporalField, data, fieldTypes)
        # TODO: rank fields?
        # fieldRanking = getFieldRanking(data)
        storeResource(redisClient, resource, primaryTemporalField, fieldTypes)
        print colored(
          "  Stored %s (%s %s) by %s" % (name, id, domain, primaryTemporalField), 
          "green"
        )

      except ValueError as e:
        print colored(
          "  [%s %s] %s:" % (id, domain, name), 
          "yellow"
        ) + " " + colored(str(e), "magenta")
      
      finally:
        count += 1
        if count % 10 == 0:
          keyCount = len(redisClient.keys("*"))
          percStored = float(keyCount) / float(count)
          print colored(
            "Processed %i streams...\nStored %i streams as temporal (%f)." 
              % (count, keyCount, percStored), 
            "cyan"
          )
  

if __name__ == "__main__":
  offset = 0
  if len(sys.argv) > 0:
    offset = int(sys.argv[1])
  run(offset)