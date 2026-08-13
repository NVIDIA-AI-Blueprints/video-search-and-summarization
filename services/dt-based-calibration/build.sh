#!/bin/bash

docker build -t autocalibration-server .

# python3.10 /opt/autocalibration/autocalibration.py --camera-spec /tmp/autocalibration/cam_specs.yaml --carla-host 127.0.0.1 --carla-port 2000  --output-dir /tmp/autocalibration/output/ --carla-map Town03

# python3 /opt/autocalibration/generate_traffic.py -f /tmp/hq.log -m HQ -n 5 -w 0 -t 30 --hybrid
