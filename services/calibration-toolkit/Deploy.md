# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

To deploy

To Build MCCT

mkvirtualenv with python3

within virtualenv, change directory to calibration/client and create file .env.production

add REACT_APP_API_ENDPOINT_BASE_URL= 'http://127.0.0.1:8003/api' to .env.production

''
# setup frontend (minify js + create django frontend app)
run `npm install`

run `npm run build`


in root folder

run `sh build_prod.sh`

you can now run the docker

docker run  -p 8003:8003 -v ~/Workspace/cartesian/calibration/data:/calibration/server/data/ -it nvcr.io/metropolis/metropolis-analytic/mdx-calibration:0.2 deploy.sh


to view app navigate to chrome with localhost:8003


# build_prod will do the following.
copy the index.html into server/frontend/templates/frontend/ directory, and copy the static folder into the server/frontend/ directory

cp build/index.html ../server/frontend/templates/frontend/


cp buildcp bui

## build container
cd ~/Workspace/*/calibration/
 sh build.sh
 docker build --network=host -t nvcr.io/metropolis/metropolis-analytic/mdx-calibration:0.3 .

docker push nvcr.io/metropolis/metropolis-analytic/mdx-calibration:0.3

# run the container
docker run  -p 8000:8000 -v ~/Workspace/cartesian/calibration/data:/calibration/server/data/ -it nvcr.io/metropolis/metropolis-analytic/mdx-calibration:0.2 deploy.sh




#dev setup
create a file in calibration/client/.env.development and add below:
REACT_APP_API_ENDPOINT_BASE_URL= 'http://127.0.0.1:8000/api'

sh build.sh

docker-compose build
docker-compose up

Start Toolkit using: localhost:3000
