import sys
sys.path.insert(0, 'd:/VS/code/Ecowise')
import device_client

devices = device_client.get_all_devices()
print('Total devices:', len(devices))
for d in devices:
    print(f"ID: {d.get('device_id')}, Name: {d.get('device_name')}, Owner: {d.get('owner')}")
