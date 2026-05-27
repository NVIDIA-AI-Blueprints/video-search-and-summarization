########################### VST & Nvstreamer deployment steps #################################


######### Prerequisites ##############

a. We have tested 500 streams on machine with below configuration

Processor: Intel(R) Xeon(R) Platinum 8352Y CPU @ 2.20GHz
CPU(s): 128
Network Card: We need minimum 2.5 Gb/s bandwidth network card to support 500 streams assuming 5Mbps streams

b. Before deploy the nvstreamer and VST for DC and k8s, Set read an write buffer size using below command:
sudo sysctl -w net.core.rmem_max=2000000 && sudo sysctl -w net.core.wmem_max=2000000

############# VST Docker Compose Deployment Steps #################

A) Untar the vst-2.1.0-26.05.2.tar.gz, Goto docker-compose directory.

B) Deploy NVStreamer:

	a. Navigate to nvstreamer directory where the tar package is untar
		cd docker-compose/nvstreamer

	b. Modify compose.env with video paths for 5 NVStreamer instances:
        NVSTREAMER_VIDEO_1=/path/to/nvstreamer/videos1
        NVSTREAMER_VIDEO_2=/path/to/nvstreamer/videos2
        NVSTREAMER_VIDEO_3=/path/to/nvstreamer/videos3
        NVSTREAMER_VIDEO_4=/path/to/nvstreamer/videos4
        NVSTREAMER_VIDEO_5=/path/to/nvstreamer/videos5

		Note: Create separate folders for each instance or upload videos via UI later.

	c. Start/Stop NVStreamer instances:
		# Start
			sudo docker compose -f docker-compose.yaml --env-file ./compose.env up --force-recreate -d

		# Stop
			sudo docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans -v

	d. Access NVStreamer instances:
		http://<Host IP>:31000/#/streamer/dashboard
		http://<Host IP>:31001/#/streamer/dashboard
		http://<Host IP>:31002/#/streamer/dashboard
		http://<Host IP>:31003/#/streamer/dashboard
		http://<Host IP>:31004/#/streamer/dashboard

	e. Note:
		Please change the ports in compose.env file in case you encounter port conflicts

C) Deploy VST:

	a. Navigate to directory where the tar package is untar
		cd docker-compose/

	b. Modify compose.env in docker-compose/ directory
		HOST_IP=<Host IP>
		VST_CONFIG_PATH=/path/to/configs/directory
		VST_VOLUME=/path/to/vst_volume/directory

	c. Configure vst_config.json:
		Set redis_server_env_var: "<Host IP>:6379"
		Set max_devices_supported: 500

	d. Configure rtsp_streams.json with NVStreamer instances:
		{
    		"Nvstreamer": [
        		{
            		"enabled": true,
            		"endpoint": "<IP>:31000",
            		"api": "/api/v1/sensor/streams",
            		"max_stream_count": 100
        		},
        		// Repeat for other instances (31001-31004)
    		]
		}
	e. If you want to increase the storage for VST then update, vst_storage.json : 
			update the total_video_storage_size_MB
			eg: "total_video_storage_size_MB": 500000

	f. Start/Stop VST:
		# Start (Standalone VST with grafana)
			sudo docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring up --force-recreate -d

		# Stop (Standalone VST with grafana)
			sudo docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring down --remove-orphans -v

	g. Access VST UI:
		http://<Host IP>:30888/vst/#/vst/dashboard

D) Object storage testing with MinIO (For testing purpose only):

	a. Navigate to directory where the tar package is untar
		cd docker-compose/

	b. Configure vst_config.json:

		# Change the following setting:
		"enable_minio": true

	c. Start/Stop VST with MinIO (Standalone VST for object storage testing):
		# Start VST with MinIO
			sudo docker compose -f docker-compose.yaml --env-file ./compose.env --profile minio --profile monitoring up --force-recreate -d

		# Stop VST with MinIO
			sudo docker compose -f docker-compose.yaml --env-file ./compose.env --profile minio --profile monitoring down --remove-orphans -v

	d. Access MinIO Service:
		MinIO Console: http://<Host IP>:9001
		MinIO API: http://<Host IP>:9000
		Credentials: admin/admin123!

	e. MinIO Configuration:
		- Default bucket: videos
		- Access Key: admin
		- Secret Key: admin123!

E) Notes:
	a. Use "Scan sensors" in VST UI to add newly uploaded streams
	b. Recording initialization for 500 streams takes 30-40 minutes
	c. To reset VST: Stop all services, delete the VST_VOLUME directory and restart all services
	d. When using multiple profiles, use separate --profile flags (e.g., --profile minio --profile monitoring) instead of comma-separated syntax

E) Known Issues:
	a. SDR may not re-add streams after rtspserver/recorder replica restart, affecting live stream availability

F) For some of the use-cases like audio, software encoding-decoding, video downloading etc. additional packages are needed. Please enable the below config in compose.env.
   	VST_INSTALL_ADDITIONAL_PACKAGES=true

G) webrtc streams testing using testWebrtcTool:
	# Start
	./testWebrtcTool vst --fps-interval 10 --csv-file ./fps-csv63 --duration 6000 --num-streams 20 --quality high --vst-endpoint <IP>:30888/vst
	
	Note:
	a. For single container use format ip:port, eg: 10.24.216.254:30888
	b. For docker-compose add "/vst" in endpoint, eg: 10.24.216.254:30888/vst




###################### VST k8s Deployment Steps #########################

## Prerequisites ##

Ensure that the local-path-provisioner and Redis charts are installed. If not, use the following commands to install them

    helm install local-path-provisioner https://helm.ngc.nvidia.com/metropolis/a3ieng/charts/local-path-provisioner-0.1.3.tgz --username='$oauthtoken' --password=<NGC_API_KEY> -f k8s-deployment/mdx-local-path-provisioner.yml

    helm install mdx-redis https://helm.ngc.nvidia.com/metropolis/a3ieng/charts/redis-0.1.0.tgz --username='$oauthtoken' --password=<NGC_API_KEY> -f k8s-deployment/mdx-redis.yml
 
##################


A) Untar the vst-2.1.0-26.05.2.tar.gz on host machine.

B) Configure NVStreamer App
	- Default 5 `Nvstreamer` instances are configured in the file 'k8s-deployment/nvstreamer-app-values.yml'.
	- Modify the file with the desired number of `nvstreamer` instances as per the requirement.

C) Deploy NVStreamer App:
	a. Helm install: 
    	helm install nvstreamer-app https://helm.ngc.nvidia.com/rxczgrvsg8nx/vst-dev/charts/nvstreamer-app-2.0.7.tgz --username='$oauthtoken' --password=<NGC_API_KEY> -f k8s-deployment/nvstreamer-app-values.yml

	b. Access NVStreamer instances:
		Once all `nvstreamer` instance pods are up, the UI will be available at:
		http://localhost:30889/nvstreamer/
		http://localhost:30889/nvstreamer-1/
		http://localhost:30889/nvstreamer-2/
		http://localhost:30889/nvstreamer-3/
		http://localhost:30889/nvstreamer-4/

	c. Upload Streams to `NVStreamer`
		After ensuring that all `nvstreamer` pods are up and in a ready state, upload your video streams using either the nvstreamer-UI or the provided script.
		Example script commands for uploading videos to each `nvstreamer` instance:

    		python3 nvstreamer_upload.py http://localhost:30889/nvstreamer/ 100 /path/to/videos-1/
    		python3 nvstreamer_upload.py http://localhost:30889/nvstreamer-1/ 100 /path/to/videos-2/
    		python3 nvstreamer_upload.py http://localhost:30889/nvstreamer-2/ 100 /path/to/videos-3/
    		python3 nvstreamer_upload.py http://localhost:30889/nvstreamer-3/ 100 /path/to/videos-4/
    		python3 nvstreamer_upload.py http://localhost:30889/nvstreamer-4/ 100 /path/to/videos-5/
    		
 
D) Configure VST App:
	- Adjust the configurations in this file 'k8s-deployment/vst-app-values.yml' according to your setup.
	- Modify parameters like maxStreamsSupported, number of replica pods, Redis configuration, `Nvstreamer:` section, and TURN server settings etc.
	- By default, configurations are set for 500 streams (5 recorder pods, 5 RTSP server pods, 1 livestream pod, 1 replay stream pod. Fetch 500 streams from 5 `nvstreamer` instances). Also max record-storage size is set to 1TB.
   
E) Deploy VST App:
	a. Helm install:
   		helm install vst-app https://helm.ngc.nvidia.com/rxczgrvsg8nx/vst-dev/charts/vst-app-2.0.10.tgz --username='$oauthtoken' --password=<NGC_API_KEY> -f k8s-deployment/vst-app-values.yml

	b. Access VST instance:
		- When all vst pods are up, then UI will be available at: [http://localhost:30888/vst/]
		- To include newly uploaded nvstreamer streams in VST, invoke scan fron vst-ui (Sensor Management -> Scan Sensors -> Submit)

G) For some of the use-cases like audio, software encoding-decoding, video downloading etc. additional packages are needed. In that case 'user_additional_install.sh' script needs to be run. Uncomment the below line from all the places in the vst-app-values.yaml.
	#/home/vst/vst_release/tools/user_additional_install.sh

H) Deploy VST with minio object store:
	a. Helm install:
   		helm install vst-app https://helm.ngc.nvidia.com/rxczgrvsg8nx/vst-dev/charts/vst-app-minio-2.0.10.tgz --username='$oauthtoken' --password=<NGC_API_KEY> -f k8s-deployment/vst-app-values.yml


########################### VST Storage Service API overview #################################

a. Upload API in actual upload mode 
    - POST /api/v1/storage/file
    - mediaFile param is mandatory
    - in metadata json, timestamp and sensorId are mandatory

Curl Command:
curl --location 'http://IP:30000/api/v1/storage/file' \
  --header 'Content-Type: multipart/form-data' \
  --form 'mediaFile=@"/path/to/video/file.mp4"' \
  --form 'metadata={
    "eventInfo": "event_related_info",
    "timestamp": epoch_time_uint64,
    "streamName": "name_associated_with_stream",
    "sensorId": "sensorId_from_which_this_originated"
  }'

Sample Curl Command :
curl --location 'http://10.24.218.241:30011/api/v1/storage/file' \
  --header 'Content-Type: multipart/form-data' \
  --form 'mediaFile=@"/Users/Videos/storage_service/violations_1_hour.mp4"' \
  --form 'metadata={
    "eventInfo": "violations_1_hour",
    "timestamp": 1730000000000,
    "streamName": "violations_1_hour_clip",
    "sensorId": "b7a1c1f2-9c0e-4d8d-8a6a-2e5f7d2e3c1b"
  }'


b. File path API SensorID based and unique ID based
    -  GET /api/v1/storage/file/<sensorId>/path?startTime=ISO_TS&endTime=ISO_TS&metadata=false
         -  example: http://<ip>:30000/api/v1/storage/file/<sensorId>/path?startTime=<ISO_TimeStamp>&endTime=<ISO_TimeStamp>&metadata=<bool>

    -  GET /api/v1/storage/file/path?id=<uniqueId>&metadata=false
         - example: http://<ip>:30000/api/v1/storage/file//path?id=<uniqueId>&metadata=true

c. Download API 
    i) SensorID based with PTS based start and end time: 
    - GET /api/v1/storage/file/{sensorId}

	  curl -X GET \
		"http://<ip>:30000/api/v1/storage/file/{sensorId}?"\
		"startTime=YYYY-MM-DDTHH:MM:SS.sssZ&"\
		"endTime=YYYY-MM-DDTHH:MM:SS.sssZ&"\
		"id=<uniqueId>&"\
		"fullLength=false"

    ii) UniqueID based with PTS based start and end time: 
    - GET /api/v1/storage/file?id=uniqueId

      curl  -X GET 'http://<ip>:30000/api/v1/storage/file?id=uniqueId&startTime=YYYY-MM-DDTHH:MM:SS.sssZ&endTime=YYYY-MM-DDTHH:MM:SS.sssZ&'

  

Refer to the Document 'VST Storage Management API Schema for VSS On Edge and DeepSearch'  for more details about API
https://nvidia.sharepoint.com/:w:/r/sites/VST/Shared%20Documents/Design/Storage_Service_API_for_VSS_on_Edge.docx?d=w9f5cb58cc3de48dab949eb41887e08c1&csf=1&web=1&e=QqaYSk

################################# Docker deployment steps #################################
	a. Docker run command
	sudo docker run -it -e ADAPTOR=vst_rtsp  --gpus all -v $PWD/vst_data:/home/vst/vst_release/vst_data \
	-v $PWD/vst_video:/home/vst/vst_release/vst_video  -v /path/to/local/volume:/home/vst/vst_release/streamer_videos \
	--net=host nvcr.io/rxczgrvsg8nx/vst-dev/vst:storage_2.1.0-25.12.1