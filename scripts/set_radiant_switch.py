#!/usr/bin/env python3
"""Set the radiant floor switch entity in options via set_supply_entity pattern."""
import requests
import time

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# The radiant floor switch entity needs to be configured through the options flow
# Let's check if there's a service for it or if it must go through the flow
print("The radiant floor switch entity is: switch.radiant_floor_contoller")
print("This must be configured through the integration's Configure UI (Options Flow)")
print("\nTo set it: Go to Settings > Devices & Services > SensorLinx > Configure")
print("In Step 1 (External Switches), select 'switch.radiant_floor_contoller'")
print("for the 'Radiant Floor Switch' field.")
