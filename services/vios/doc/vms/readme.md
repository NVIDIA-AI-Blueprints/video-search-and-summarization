# Video Storage Toolkit
## **Introduction**
#### In the need of Intelligent Video Analytics (IVA) solution in almost all domains, developers need to interact with a system which will provide all the required abstraction for video management. 
#### NVIDIA’s Video Storage Toolkit (VST) delivers a complete solution for Video Management. A VST is responsible for a few critical tasks in end to end solutions that leverage devices as sensors. The VST allows end users to add or remove devices, configure, and control them and create a policy driven storage and archival of the video feeds at appropriate media granularity. The VST enables retrieval of previously stored video content. Finally, it enables local and remote dashboards to play one or more such streams synchronously. AI IVA solutions need to access such device streams in real time streaming analytics as well as retrieval and visualization.
---
## **Features**
1. Supports ONVIF S-profile devices, ONVIF discovery with control and data flow.
2. Add devices manually by IP address and/or RTSP URLs
3. Video storage and aging policy
4. RTSP proxy url in pass-through and multi-cast mode
5. Media streaming using webRTC protocol for live and recorded videos.
6. Provide RESTful APIs to write client application to control and configure VST.
7. Support Redis message bus to publish device add/remove events.
8. Prometheus/Grafana integration to publish some VST stats.
9. Provide sample web-based client.
10.	Configure devices remotely
11. Policy driven video recording
12. Derive insights about the current status of VST
---

### Getting Started
#### [**1) VST Architecture Overview**](#overview-of-system)
#### [**2) VST Block Diagram**](#block-diagram)
#### [**3) VST Typical Setup**](#typical-setup)
---
### Quick Start Guide
#### [**1) Deploy VST Package**](#deploy-vst-package)   
* #### [**Deployment Prerequisites**](#deployment-prerequisites)
* #### [**Deployment Script**](#deployment-script)
* #### [**Understand Command Line Arguments**](#understand-command-line-arguments)


#### [**2) Quick Test VST Application**](#quick-test-vst-application)

#### [**3) VST Configuration**](#configuration)
* #### [**VST Config File**](#1-vst-config-file)
* #### [**Adaptor Config File**](#2-adaptor-config-file)
* #### [**In-built Adaptors**](#3-in-built-adaptors)

---

### [Application Development Guide](#application-development-guide)
#### [**1) Adaptor Architecture**](#adaptor-architecture)
#### [**2) Quick Start Application Development**](#quick-start-application-development)
#### [**3) How to access API Documentation**](#how-to-access-api-documentation)

---

## **Overview of System**
![alt text](overview.jpg)

---

## **Block Diagram**
![alt text](building_block.jpg)

---

## **Typical Setup**
![alt text](typical_setup.jpg)

---
## **Deploy VST Package**   

### **Deployment Prerequisites**
**SSL certificate:** 
  * VST by default generates a self-signed certificate (***self_signed_certificate.pem***) at the time of installation
  * This certificate is generated at location configured using ***vst_data_path*** config parameter
  * Users can obtain public CA certificate and rename it to ***ca_certificate.pem*** and place it in ***vst_data_path***

**Installation of STUN / TURN Server:**  
Installation and configuration of these servers are mandatory for webRTC streaming
Below are the NAT type requirements for STUN/TURN :

|Case | NAT types	                          | STUN/TURN requirement			   |
|-----|-----------                            |----------------------			   |
|1    | Symmetric to Symmetric			      |	TURN							   |
|2    | Symmetric to Port-Restricted Cone     |	TURN							   |
|3	  |	Symmetric to Address-Restricted Cone  |	STUN (but probably not reliable)   |
|4    |	Symmetric to Full Cone				  |	STUN        					   |
|5    |	Everything else						  |	STUN							   |

Depending on your network type, user may choose to install STUN and / or TURN servers  
You can set the STUN server using ***stunurl*** config parameter  
You can set the TURN server using ***turnurl*** config parameter  

VST webUI is verified with [coturn](#http://github.com/coturn/coturn) server, below are the installation steps:
```
sudo apt-get install coturn
```
**Note:** TURN server is not mandatory, this is required only if Client and Server are in different networks

### **Deployment Script**
* This section helps users and developers to install VST application
* Deployment script provides a comprehensive list of options to install VST. This script helps user / developer to install and run tar package or docker container image
 
#### **Deployment script options**
```
 ./deploy_vst.sh
         --launch_mode <package/container>
         --vst_config_file <Path of vst_config.json>
         --adaptor_config_file <Path of adaptor_config.json>
         --rtsp_streams_file <Path of rtsp_streams.json>
         --docker_image <Path of docker container image>
         --vst_host_data_path <Host path for vst_data (configs, db etc.)>
         --vst_host_video_path <Host path for recorded_videos>
         --debug_level 1 - 5
           1 - error
           2 - warning
           3 - info
           4 - verbose
           5 - more verbose
         --log_to_file <file logging path>

```
#### **Typical usage of deployment script**

1. Run VST from local tar package  
	```
	./deploy_vst.sh --launch_mode package
	```

2. Run VST from package & overwrite the VST config with user provided config file  
	```
	./deploy_vst.sh --launch_mode package --vst_config_file /path/to/vst_config.json
	```

3. Run VST using Docker Container and automatically create data & video directories at root path
	```
	./deploy_vst.sh --launch_mode container --docker_image <link_to_container_image>
	```

4. Run VST using Docker Container and use user provided host directories for data & videos. Container will use config files if they are available at host path */data/vst_data*  
	```
	./deploy_vst.sh --launch_mode container --docker_image <link_to_container_image> --vst_host_data_path /data/vst_data --vst_host_video_path /data/vst_video
	```

### **Understand Command Line Arguments**

* Print all supported command-line arguments of VST application
```
./launch_vst --help
```
* Typical output of above command will be
```
./launch_vst
        --vstConfigFile <path_to_vst_config.json>
        --adaptorConfigFile <path_to_adaptor_config.json>
		--rtspStreamsFile <path_to_rtsp_streams.json>
        --debug-level 1 - 5
          1 - error
          2 - warning
          3 - info
          4 - verbose
          5 - more verbose
        --log-to-file - To enable file logging
```

| Argument                    | Description                                              | Value                                  |
| --------------------------- | ----------------------------------------------           | -----------------------------          | 
| - *--vstConfigFile*         | Path to [VST Config file](#1-vst-config-file)            | */path/to/vst-config/file.json*        |
| - *--adaptorConfigFile*     | Path to [Adaptor Config file](#2-adaptor-config-file)    | */path/to/adaptor-config/file.json*    |
| - *--rtspStreamsFile*       | Path to [RTSP streams file](#rtsp-streams-file)          | */path/to/rtsp-streams/file.json*    |
| - *--debug-level*           | Logging level of application                             | 3                                      |
| - *--log-to-file*           | To enable file logging                                   | */path/to/log/file.log*                |

## **Quick Test VST Application**
#### To quickly check VST is properly set up and launched, test it with any web browser or curl command using any following method

### **1) Launch sample webUI**
* Launch web browser
* In the address bar enter IP/domain Address of host on which VST is running followed by port number.
* Example : <IP_ADDRESS>:<PORT_NUMBER>
* Sample : *https://192.168.1.23:30000*

### **2)  Curl Command**
* Launch Linux Terminal
* Execute curl command with IP Address of host on which VST application is running followed by port number followed by VST API
* Example: *curl <IP_ADDRESS>:<PORT_NUMBER>/api/{api_name}*
* Sample curl command: *curl https://192.168.1.23:30000/api/help*
* Check following few highlighting functionalities using CURL Commands
1) **Get all device's details :** GET *api/device/list*

	Sample Request
	```
	curl -X 'GET' \
  	'https://IP_ADDRESS:PORT_NUMBER/api/device/list' \
  	-H 'accept: application/json'
	```
	Sample Response on success, otherwise empty response
	```
	[
		{
			"firmware_version" : "",
			"hardware" : "",
			"hardware_id" : "",
			"id" : "Amcrest",
			"ip" : "",
			"location" : "",
			"manufacturer" : "",
			"name" : "Amcrest",
			"position" : 
			{
				"depth" : "",
				"direction" : "",
				"field_of_view" : "",
				"gps" : 
				{
					"latitude" : "",
					"longitude" : ""
				}
			},
			"serial_number" : ""
		}
	]
	```
	---
2) **Get device status :** GET *api/device/status*

   	Sample Request
	```
	curl -X 'GET' \
  	'https://IP_ADDRESS:PORT_NUMBER/api/device/status' \
  	-H 'accept: application/json'
	```

	Sample Response on success, otherwise NULL
	```
	{
		"Amcrest" : 
		{
			"error_code" : "NoError",
			"error_message" : "No Error",
			"name" : "Amcrest",
			"state" : "online"
		}
	}
	```
	* Developers should refer [Application Development Section](#application-development-guide) for comprehensive list of APIs and its usage

	---
### **Configuration**
#### **1) VST Config File**
####  Following table describes the parameters in vst config file, this configuration file should be provided as command-line option while launching application
e.g. *launch_vst --vstConfigFile </path/to/vst-config/file.json>*

| Key                                          | Description                                              | Typical Value                       |
| ---------------------------                  | ----------------------------------------------           | -----------------------------       | 
| **network**                                  | **Section to define all Network related parameters**            
| - *"http_port"*                              | HTTP port number for accessing VST webservices           | "30000"                                |
| - *"server_domain_name"*                     | Server domain name                                       | "vst-service"                       |
| - *"stunurl"*                                | STUN server address                                      | ["stun.l.google.com:19302"]         |
| - *"turnurl"*                                | TURN server address                                      | ["turn.server.url]"                 |
| - *"max_webrtc_connections"*                 | Max number of webrtc connections at a given time         | 100                                 |
| - *"webservice_access_control_list"*         | An Access Control List allows restrictions to be put on the list of IP addresses which have access to the web server. The ACL is a comma separated list of IP subnets, where each subnet is pre-pended by either a - or a + sign. A plus sign means allow, where a minus sign means deny. If a subnet mask is omitted, such as -1.2.3.4, this means to deny only that single IP address.                      																														  | "+10.42.0.0/32"                     |
| - *"rtsp_server_port"*                       | User given rtsp port number                              | 8554                                |
| - *"rtsp_preferred_network_iface"*           | Preferred network interface for RTSP streaming           | "eth1"                              |
| - *"socket_buffer_size"*                     | Socket buffer size, to avoid frame drop in bytes         | 2000000                             |
| - *"stream_monitor_interval_secs"*           | Stream Monitor interval in seconds                       | 2                                   |
|                                                                                                                                               |
| **onvif**                                    | **Section to define all ONVIF related parameters**
| - *"device_discovery_timeout_secs"*          | Device discovery timeout to receive probe match message from device | 10                                  |
| - *"onvif_request_timeout_secs"*             | Timeout to receive ONVIF command response from device    | 10                                  |
| - *"device_discovery_interfaces"*            | Network interface for device discovery e.g eth0, eth1    | [eth0]                              |
| - *"max_devices_supported"*                  | Limiting maximum number of devices                       | 100                                 |
| - *"bitrate_kbps"*                           | Default value of bitrate setting on device               | 8000                                |
| - *"framerate"*                              | Default value of framerate setting on device             | 30                                  |
| - *"resolution"*                             | Default value of resolution setting on device            | "1920x1080"                         |
| - *"max_gov_length"*                         | Default value of GOV length setting on device            | 60                                  |
|                                                                                                                                               |
| **data**                                     | **Section to define all VST data related parameters**
| - *"recorded_video_dir_root"*                | Path to root folder where recorded videos to be stored   | "./vst_video/"                       |
| - *"total_video_storage_size_MB"*            | Total size available to store recorded videos            | 10000                               |
| - *"vst_data_path"*                          | Path where VST related data files are created            | "./vst_data/"                        |
| - *"storage_threshold_percentage"*           | % value of storage occupancy when aging policy triggers  | 95                                  |
| - *"storage_monitoring_frequency_secs"*      | Monitor storage usage with frequency in seconds          | 2                                   |
| - *"supported_video_codecs"*                 | Video codec supported for video recording                | ["h264"]                            |
| - *"supported_audio_codecs"*                 | Audio codec supported for audio recording and decode     | ["pcmu"]                            |
| - *"enable_aging_policy"*                    | Enable/Disable aging policy of video files               | true                                |
| - *"always_recording"*                       | Set recording to always ON                               | true                                |
|                                                                                                                                               |
| **notifications**                            | **Section to define all Notifications related parameters**
| - *"enable_notification"*                    | Enable notification of device events on redis bus        | false                               |
| - *"notify_vst_event"*                       | Redis event name                                         | "vst.event"                         |
| - *"redis_server_env_var"*                   | Redis server address:port                                | "REDIS_SVC_SERVICE_HOST:6379"       |
|                                                                                                                                               |
| **debug**                                    | **Section to define all Debug related parameters**
| - *"enable_perf_logging"*                    | Enable/Disable perf logging                              | true                                |
| - *"enable_qos_monitoring"*                  | Enable/Disable QOS logging                               | true                                |
| - *"qos_logfile_path"*                       | OS log file path                                         | "/root/vst_release/webroot/log/"    |
| - *"qos_data_capture_interval_sec"*          | QOS log capture interval                                 | 1                                   |
| - *"qos_data_publish_interval_sec"*          | QOS log publish interval                                 | 5                                   |
| - *"enable_gst_debug_probes"*                | Enable/Disable gstreamer probes                          | true                                |
| - *"enable_prometheus"*                      | Enable/Disable stats update on prometheus                | false                               |
| - *"prometheus_port"*                        | prometheus port number                                   | "8080"                              |
| - *"enable_highlighting_logs"*               | Enable certain logs highlighting by special colors       | true                                |
|                                                                                                                                               |
| **security**                                 | **Section to define all security related parameters**
| - *"use_https"*                              | Enable/Disable https                                     | true                                |
| - *"use_rtsp_authentication"*                | Enable/Disable RTSP proxy stream authentication          | true                                |
| - *"use_http_digest_authentication"*         | Enable/Disable HTTP user authentication                  | true                                |

**Prometheus** : Prometheus is used to monitor events using a time-series database. VST acts as Prometheus client, use ***"enable_prometheus"*** config parameter to enable / disable the client instance is VST.    
**Note:**
   1) VST assumes Prometheus Server is running (as a separate service)
   2) The prometheus events are visible at *<host_ip_address>:<prometheus_port>/metrics* endpoint.
   3) The prometheus events are explicitly pulled by server and not pushed by client

**Redis** : Redis is a in-memory data structure store, used as a database, cache, and message broker. VST can send alerts using Redis to other services or applications. Use ***"enable_notification"*** config parameter to enable / disable the service. The Redis event to be used can be specified using the option ***"notify_vst_event"***, this event name should be used by other services / application to subscribe the redis messages from VST. Redis client needs the server address with port, which can be specified using ***"redis_server_env_var"*** option.  
**Note:** Provide the address of the Redis server as an environment variable.
For example, while running VST in a docker environment, *-e* option can be used with the docker run command to specify the environment variable, like, *-e REDIS_SVC_SERVICE_HOST=127.0.0.1*

* Typical format of __*vst_config.json*__
```json
{
	"network":
	{
		"key1": "value1",
		"key2": "value2"
	},
	"onvif":
	{
		"key1": "value1",
		"key2": "value2"
	},
	"data":
	{
		"key1": "value1",
		"key2": "value2"
	},
	"notifications":
	{
		"key1": "value1",
		"key2": "value2"
	},
	"debug":
	{
		"key1": "value1",
		"key2": "value2"
	},
	"security":
	{
		"key1": "value1",
		"key2": "value2"
	}
}
```

#### **2) Adaptor Config File**
#### Following table describes the parameters in adaptor config file, this configuration file should be provided as command-line option while launching application
e.g. *launch_vst --adaptorConfigFile </path/to/adaptor-config/file.json>*
* **Note: Enable only one adaptor at a time in *adaptor_config.json*, multiple adaptors are not supported**

| Key                                             | Description                                          | Typical Value                          |
| ---------------------------                     | ---------------------------------                    | -----------------------------          | 
| - *"enabled"*                                   | Enable/Disable particular adaptor                    | true                                   |
| - *"id"*                                        | Unique string to identify adaptor                    | "044bc643-33c5-479a-b988-10d0bbc4e05c" |
| - *"name"*                                      |  Name                                                | "onvif"                            |
| - *"type"*                                      |  Type                                                | "vst"                                  |
| - *"need_rtsp_server"*                          | Enable/Disable RTSP Server                           | true                                   |
| - *"need_recording"*                            | Enable/Disable Recording feature                     | true                                   |
| - *"“control_adaptor_lib_path"*                 | Control Adaptor library path to be used              | */path/to/control/adaptor/lib.so*      |
| - *"discovery_adaptor_lib_path"*                | Discovery Adaptor library path to be used            | */path/to/discovery/adaptor/lib.so*    |

* Typical format of __*adaptor_config.json*__
```json
{
	"vst":
	[
		{
            "enabled":true,
            "id":"044bc643-33c5-479a-b988-10d0bbc4e05c",
            "name":"onvif_auto_discovery",
            "type":"vst",
            "need_rtsp_server": true,
            "need_recording": true,
            "control_adaptor_lib_path":"prebuilts/arch/onvif_client.so",
            "discovery_adaptor_lib_path":"prebuilts/arch/onvif_discovery.so"​
        },
        {
            "enabled":false,
            "id":"6cdec7d7-0f30-450c-a78a-756c3e132fd3",
            "name":"vst_rtsp_manually",
            "type":"vst",
            "need_rtsp_server": true,
            "need_recording": true,
            "control_adaptor_lib_path":"prebuilts/arch/rtsp_streams.so"
        }
    ]
}
```

#### **3) In-built Adaptors**
#### **1) ONVIF Control and Auto Discovery Adaptor** : 
  1. This adaptor provides ONVIF Control Client and Device discovery using WS-Discovery Protocol
  2. **onvif_client.so** : This lib implements ONVIF Control Adaptor which is a ONVIF Client.
  3. **onvif_discovery.so** : This lib implements ONVIF Discovery Adaptor which detects addition and removal of ONVIF devices (IP Cameras) in same subnet.
   
#### **2) RTSP Stream Adaptor** : 
  1. This adaptor supports adding manually RTSP URLs and defining output RTSP URLs
  2. **rtsp_streams.so** : This lib implements RTSP Stream Adaptor.

#### RTSP streams file  
Following table describes the parameters in RTSP streams file, this configuration file should be provided as command-line option while launching application
e.g. *launch_vst --rtspStreamsFile </path/to/rtsp-streams/file.json>*

| Key                                             | Description                                          | Typical Value                          |
| ---------------------------                     | ---------------------------------                    | -----------------------------          | 
| - *"enabled"*                                   | Enable/Disable particular RTSP Stream                | true                                   |
| - *"rtsp_in"*                                   | RTSP URL as input to VST                             | "rtsp://admin:admin@10.20.30.40/h264"  |
| - *"rtsp_out_path"*                             | User define path name                                | "Brand_1"                              |

Note: The typical RTSP URL format : *rtsp://<VST_IP_ADDRESS>:<PORT_NUMBER>/live/rtsp_out_path*
* Typical format of __*rtsp_streams.json*__
```json
{
    "streams":
        [
            {
                "enabled":true,
                "rtsp_in":"rtsp://admin:admin@10.20.30.40/h264",
                "rtsp_out_path":"Brand_1"
            },
            {
                "enabled":true,
                "rtsp_in":"rtsp://admin:admin@10.20.30.41/h264",
                "rtsp_out_path":"Brand_2"
            }
        ]
}
```
-----

## **Application Development Guide**
### **Adaptor Architecture**
![alt text](adaptor_architecture.jpg)

### **Adaptor Architecture has 2 interfaces**
### **1) Device Discovery Interface** : This adaptor interface is to discover devices, eg: ONVIF Protocol or UPnP Protocol
* A new Device Discovery Adaptor should implement ***IDeviceDiscoveryInterface*** Class (refer ***include/device_discovery_adaptor.h***)
* Implement the following methods to create and destroy adaptor object
```
typedef IDeviceDiscoveryInterface* createDiscoveryObject();​
typedef void destroyDiscoveryObject ( IDeviceDiscoveryInterface* object );
```
### **2) Device Control Interface** : This adaptor interface is to control the devices eg: ONVIF Control Protocol
* A new Device Control Adaptor should implement ***IDeviceControlInterface*** Class (refer ***include/device_control_adaptor.h***)
* Implement the following methods to create and destroy adaptor object
```
typedef IDeviceControlInterface* createObject();​
typedef void destroyObject( IDeviceControlInterface* object );
```

#### Both the interfaces can be loaded using Adaptor loader.
-----

### **Quick Start Application Development**
* This section helps developer to get started with application development using VST APIs.  
* VST offers a set of REST APIs for client applications, using these APIs, developers have the ability to access all features of VST   
* VST APIs are compliant to OpenAPI standards, so developers are able interact with VST via HTTP GET / POST request. 

Launch VST application using deployment script mentioned [above](#deployment-script) and check following few highlighting functionalities using CURL Commands

1) **Get all device's details :** GET *api/device/list*

	Request type : GET  
	Sample Request URL
	```
  	https://IP_ADDRESS:PORT_NUMBER/api/device/list
	```
	Sample Response on success, otherwise empty response
	```
	[
		{
			"firmware_version" : "",
			"hardware" : "",
			"hardware_id" : "",
			"id" : "Amcrest",
			"ip" : "",
			"location" : "",
			"manufacturer" : "",
			"name" : "Amcrest",
			"position" : 
			{
				"depth" : "",
				"direction" : "",
				"field_of_view" : "",
				"gps" : 
				{
					"latitude" : "",
					"longitude" : ""
				}
			},
			"serial_number" : ""
		}
	]
	```
	---
2) #### **Get live and replay stream details(RTSP URLs) :** GET *api/device/streams*

	Request type : GET  
	Sample Request URL
	```
  	https://IP_ADDRESS:PORT_NUMBER/api/device/streams
	```

	Sample Response on success, otherwise NULL
	```
	[
		{
			"1d22e7d3-e3fb-42d8-b48d-24933a2ea556" :
			[
				{
					"bitrate" : "8000",
					"codec" : "H264",
					"framerate" : "30",
					"govlength" : "25",
					"id" : "1d22e7d3-e3fb-42d8-b48d-24933a2ea556",
					"is_main" : true,
					"live_url" : "rtsp://10.24.216.202/live/1d22e7d3-e3fb-42d8-b48d-24933a2ea556",
					"name" : "Sony",
					"replay_url" : "rtsp://10.24.216.202/vod/1d22e7d3-e3fb-42d8-b48d-24933a2ea556",
					"resolution" : "1920x1080"
				},
			]
		}
	]
	```
	---
10) **Get device status :** GET *api/device/status*

	Request type : GET  
   	Sample Request URL
	```
  	https://IP_ADDRESS:PORT_NUMBER/api/device/status
	```

	Sample Response on success, otherwise NULL
	```
	{
		"Amcrest" : 
		{
			"error_code" : "NoError",
			"error_message" : "No Error",
			"name" : "Amcrest",
			"state" : "online"
		}
	}
	```
	---
2) **Add new device (applicable for [ONVIF](#1-onvif-control-and-auto-discovery-adaptor-) Adaptor) :** POST *api/device/new*   
   
	
	This API can be used to add device using IP Address or using RTSP URL.  
	**Add device using IP Address :** *url* field can be omitted  
	**Add device using RTSP URL :** *ip, username and password* fields can be omitted  
	Request type : POST  
	Request Body
	```
	{
		"url": "string",
		"name": "string",
		"location": "string",
		"hardware": "string",
		"ip": "string",
		"username": "string",
		"password": "string",
		"manufacturer": "string",
		"serial_number": "string",
		"firmware_version": "string",
		"hardware_id": "string"
	}
	```
	Sample Response on success, otherwise [error](#error)
	```
	true
	```
	---
3) **Authenticate devices (applicable for [ONVIF](#1-onvif-control-and-auto-discovery-adaptor-) Adaptor) :** POST *api/{device-id}/credentials*

	Request type : POST  
	device-id : Device ID retrieved using, *api/device/list*  
	Request Body
	```
	{
		"password": "string",
		"username": "string"
	}
	```
	Sample Response on success, otherwise [error](#error)
	```
	true
	```
	---
4) **Start Recording of device streams :** POST *api/device/{device-id}/record*  

	Request type : POST  
	device-id : Device ID retrieved using, *api/device/list*  
	Request Body
	```
	{
		"action": "start"
	}
	```
	Sample Response on success, otherwise [error](#error)
	```
	true
	```
	---
5) **Stop Recording of device streams :** POST *api/device/{device-id}/record*  

	Request type : POST  
	device-id : Device ID retrieved using, *api/device/list*  
	Request Body
	```
	{
		"action": "stop"
	}
	```
	Sample Response on success, otherwise [error](#error)
	```
	true
	```
	---
6) **Play Live and Recorded stream with webRTC :**

   Refer webRTC playback example from VST package

7) **Initiate scan for new devices in subnet (applicable for [ONVIF](#1-onvif-control-and-auto-discovery-adaptor-) Adaptor) :** POST *api/device/scan*

	Request type : GET  
   	Sample Request
	```
	https://IP_ADDRESS:PORT_NUMBER/api/device/scan
	```
	Sample Response on success, otherwise NULL
	```
	true
	```
	---

-----

### **How to access API Documentation**
 Developers can access API documentation [here](api.md) or if developer has VST application installed then, find the API documentation:
```
https://<IP_ADDRESS>:<PORT_NUMBER>/doc/vst.html
```
replace IP_ADDRESS and PORT_NUMBER with VST Server IP and corresponding port number (default is 30000)   
You will be able to see the list of VST APIs, and you can click and try the APIs.

---
#### **Error**

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| error_code | string | _Example:_ `"VSTInternalError"` | Yes |
| error_message | string | _Example:_ `"VST internal processing error"` | Yes |

**VST Errors  **
| HTTP Code 	 | Error Code 				| Error Message                              |
|------	 		 |------------				|---------------                             |
|0       		 |NoError            		| No Error                                   |
|403     		 |DeviceUnauthorizedError   | Device is not authorized                   |
|401     		 |ClientUnauthorizedError   | Client is not authorized                   |
|400     		 |InvalidParameterError     | Invalid or out of range parameters         |
|404     		 |DeviceNotFoundError       | Device not found OR device id is not valid |
|405     		 |MethodNotAllowedError     | Method Not Allowed              			 |
|408     		 |DeviceRequestTimeoutError | Request Timeout              				 |
|500     		 |CommunicationError        | Device communication error                 |
|500     		 |VSTInternalError          | VST internal processing error              |
|501     		 |VSTNotSupportedError      | Operation/Action not supported             |
|507     		 |VSTInsufficientStorage    | Insufficient Storage              	     |

----
