#Copyright (c) 2009-2023, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

To Build MCCT

mkvirtualenv with python3

create a file in calibration/client/.env.development and add below:
REACT_APP_API_ENDPOINT_BASE_URL= 'http://127.0.0.1:8000/api'

sh build.sh

docker-compose build
docker-compose up

Start Toolkit using: localhost:3000