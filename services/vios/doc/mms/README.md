<!--- Copyright (c) 2019-2020, NVIDIA CORPORATION.  All rights reserved. --->

<h1>Media Service</h1>
<h2>Introduction</h2>
<p>Developers creating end to end AI enabled Intelligent Video Analytics solutions need to interact with video management systems (VMS). A VMS is responsible for a few critical tasks in end to end solutions that leverage cameras as sensors. The VMS allows end users to add or remove cameras, configure and control them and create a policy driven storage and archival of the video feeds at appropriate media granularity. The VMS enabes retrieval of previously stored video content. Finally it enables local and remote dashboards to play one or more such streams synchronously.</p>
<p>AI IVA solutions need to access such camera streams in real time streaming analytics as well as retrieval and visualization. Most popular legacy VMS systems do not have easy to use API interfaces for most of the AI pipelines that are generally Unix-based. The Metropolis Data and Analytics toolkit (MDAT) contains a Media Service module that aims to abstract the functionalities of any VMS being used and provide a set of common APIs to Media Service clients.</p>
<h3>Architecture</h3>
<img src="doc/MS_architecture.svg" alt="MS Architecture Diagram">
<p>Media Service is designed as a scalable project which supports pluggable adaptors/plugins, i.e. we need to write an adaptor code to translate the VMS APIs to Media Service APIs. These pluggable adaptors help to add any 3rd party VMS in Media Service which supports SOAP, REST or any other proprietary APIs.</p>
<h3>Media Service Features</h3>
<ul>
<li>Supports pluggable adaptors to use any 3rd party VMS</li>
<li>Provides a set of REST APIs to interact with VMS</li>
<li>Supports interaction with RTSP Server to fetch live and recorded streams</li>
<li>Provides WebRTC media streaming of live and recorded VMS streams</li>
</ul>
<h3>Internal Modules</h3>
<img src="doc/MS_internals.svg" alt="MS Internal Diagram">
<ul>
<li><strong>Adaptors</strong>: Specific to each third party party VMS i.e. Milestone Adaptor</li>
<li><strong>Media Service</strong><strong>Adaptor Abstraction </strong>: This class translates the VMS protocol to Media Service protocol</li>
<li><strong>HTTP Listener</strong> : This serves as HTTP Listener and forwards the HTTP requests to corresponding modules</li>
<li><strong>HTTP Server </strong>: To serve REST API requests from clients</li>
<li><strong>Third-party Libraries</strong>
<ul>
<li><strong>sqlite </strong>: For database management to store event metadata</li>
<li><strong>libcurl </strong>: REST and SOAP client side library</li>
<li><strong>libjson </strong>: A JSON reader and writer library</li>
<li><strong>libxml2 </strong>: XML C parser</li>
<li><strong>civetweb </strong>: HTTP server used for webrtc signalling</li>
</ul>
</li>
<li><strong>webRTC</strong> : This supports Real-Time Communications (RTC) capabilities in the browser to stream recorded and live videos</li>
<li><strong>live555 </strong>: C++ RTSP client side library</li>
</ul>
<h2>Adding a new Adaptor/plugin</h2>
<p>Media Service supports connections to third party party VMS using pluggable adaptors</p>
<img src="doc/MS_new_adaptor.svg" alt="MS New Adaptor Diagram">
<p>To implement a new adaptor:</p>
<ul>
<li>A new VMS Adaptor should implement<strong><em> IVMSAdaptor Class </em></strong>(refer <em>include/vms_adaptor.h</em>)</li>
<li>Implement the following methods
<ul>
<li><strong><em>IVmsAdaptor* createObject();</em></strong>
<ul>
<li><strong><em>return new &lt;vms_Adaptor_class&gt;</em></strong></li>
</ul>
</li>
<li><strong><em>void destroyObject( IVmsAdaptor* object );</em></strong>
<ul>
<li><strong><em>delete &lt;vms_Adaptor_object&gt;</em></strong></li>
</ul>
</li>
<li>Create a new json file or populate the existing json file with the following VMS details
<ul>{ </ul>
<ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em>"<strong>enabled</strong>": &lt; boolean value &gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_id</strong>": &lt; globally unique id&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_name</strong>": &lt; human readable name&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_type</strong>": &lt; type of vms&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_ip</strong>": &lt; IP Address of VMS Server&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_user</strong>": &lt; user name&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_password</strong>": &lt; password&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_port"</strong>: &lt; port number&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>adaptor_lib_path</strong>": &lt; path/to/vms/library&gt;</em></ul>
</ul>
<ul>},</ul>
</ul>
</li>
</li>
</ul>

<ul>
<li>Once Media Service is connected to VMS it gets the related information from the third party VMS and Media Service then registers the REST APIs in HTTP server to expose the APIs to clients.</li>
<li>Media Service application provides the following command-line option to specify adaptor_config.json
<ul>
<li><strong><em>--adaptorConfigFile &lt;path_to_adaptor_config&gt;</em></strong></li>
</ul>
</li>
</ul>
<h2>Setup</h2>
<p>This section shows the implementation of Media Service for the popular Milestone VMS.</p>
<img src="doc/MS_setup.png" alt="MS Setup">
<p>Media service is deployed in an Ubuntu 18.04 container. The developer needs to prepare vms_config.json filled with required details. This config file should be provided as a command-line argument.</p>

<p>The Milestone VMS installation requires a Windows machine. Milestone Xprotect Corporate version is preferred. Media Service is communicating to Milestone VMS through ONVIF Bridge, so need to install Milestone ONVIF Bridge on the same Windows machine.</p>
<p>Media Service Test WebUI is connected to Media Service Application using webRTC protocol, please refer to the webRTC section below for more details.</p>
<p>For more details with Milestone VMS installation please refer link below<br /><a href="https://doc.milestonesys.com/mc/pdf/2020r2/en-US/MilestoneXProtectVMSproducts_GettingStartedGuide_en-US.pdf">Getting started guide - XProtect VMS 2020 R2</a></p>

<h2>Configuration options</strong></h2>
<p>Media Service supports options configurable via config json</p>
<p>Description of the options:</p>
<ul>
<li><em><strong>http_port</strong>: http port to use </em></li>
<li><em><strong>stunurl</strong>: stunurl address </em></li>
<li><em><strong>onvif_request_timeout_secs</strong>: onvif request timeout in seconds </em></li>
<li><em><strong>enable_perf_logging</strong>: enable-disable VMS perf logging </em></li>
<li><em><strong>max_webrtc_connections</strong>: maximum number of peer connections allowed </em></li>
<li><em><strong>webservice_access_control_list</strong>: limit the web service to specified subnets or ip addresses </em></li>
<li><em><strong>vms_db_path</strong>: directory where vms database will be created </em></li>
<li><em><strong>max_cameras_supported</strong>: maximum number of cameras that the user can add </em></li>
<li><em><strong>video_metadata_server</strong>: video metadata server address </em></li>
<li><em><strong>video_metadata_query_batch_size_num_frames</strong>: batch size for fetching video metadata </em></li>
</ul>
<p>
Create a new json file or populate the existing json file with the following options
</p>
<ul>
<ul>{ </ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em>"<strong>http_port</strong>": &lt; string &gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>stunurl</strong>": &lt; string&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>onvif_request_timeout_secs</strong>": &lt; integer&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>enable_perf_logging</strong>": &lt; boolean&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>max_webrtc_connections</strong>": &lt; integer&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>webservice_access_control_list</strong>": &lt; string&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>vms_db_path</strong>": &lt; string&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>max_cameras_supported</strong>": &lt; integer&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>video_metadata_server</strong>": &lt; string&gt;,</em></ul>
<ul>&nbsp;&nbsp;&nbsp;&nbsp;<em> "<strong>video_metadata_query_batch_size_num_frames</strong>": &lt; integer&gt;</em></ul>
<ul>}</ul>
</ul>

<li>Media Service application provides the following command-line option to specify vms_config.json
<ul><ul>
<li><strong><em>--vstConfigFile &lt;path_to_vst_config&gt;</em></strong></li>
</ul></ul>
</li>
</ul>

<h2>Quick Start</h2>
<p>To quickly test if Media Service is properly set up and launched, one can test it with any web browser or curl command,
launch Media Service and perform any one of below mentioned tests.</p>
<h5>A) Browser</h5>
<ul>
<li>Launch web browser</li>
<li>In the address bar enter IP Address of host on which Media Service is running followed by port number followed by Media Service API to test.
<ul>
<li>Example : <strong><em>&lt;IP_ADDRESS&gt;:&lt;PORT_NUMBER&gt;/api/&lt;API NAME&gt;<br /></em></strong>Sample URL: <a href="http://192.168.1.23:81/api/help"><strong><em>http://192.168.1.23:81/api/help</em></strong></a></li>
</ul>
</li>
<li>It is expected that web browser should print the JSON response received from Media Service</li>
</ul>
<h5>B) Curl Command</h5>
<ul>
<li>Launch Linux Terminal</li>
<li>Execute curl command with IP Address of host on which Media Service is running followed by port number followed by Media Service API to test.
<ul>
<li>Example: <strong><em>curl &lt;IP_ADDRESS&gt;:&lt;PORT_NUMBER&gt;/api/&lt;API NAME&gt;</em></strong><br /> Sample curl command: <strong><em>curl </em></strong><a href="http://192.168.1.23:81/api/help"><strong><em>http://192.168.1.23:81/api/help</em></strong></a></li>
</ul>
</li>
<li>It is expected that the JSON response received from Media Service should be printed in terminal</li>
</ul>
<h2>APIs and Interaction with other Modules</h2>
<h4><strong>Media Service REST APIs to interact with clients</strong></h4>
<p>Information about all Media Service APIs can be found at <a href="swagger.yaml"><strong><em>MMS API documentation</em></strong></a>

<h4><strong>Sequence Diagram to depict interaction between Metropolis Data and Analytics Toolkit (MDAT) Components</strong></h4>
<p>A common requirement in end to end application development that the Metropolis Data and Analytics Toolkit (MDAT) enables is the ability for the analytics framework to detect events of interest such as anomalies, based on the metadata extracted by the perception pipeline (such as NVIDIA Deepstream) and the ability to fetch a specific video clip from the VMS corresponding to this detected event or anomaly. The sequence diagram below explains how this flow works with the various MDAT modules working together.</p>
<img src="doc/MS_seqdiagram1.svg" alt="MS Sequence Diagram Metropolis Data and Analytics Toolkit (MDAT) Components">

<ul>
<li>Media Service connects to VMS and gets the camera info list from 3rd party VMS</li>
<li>Perception engine calls REST APIs to get required information using the REST APIs like server information, live stream URL list etc.</li>
<li>Perception engine (NVIDIA Deepstream in this case) starts inferencing on live stream and generates metadata based on object detection and tracking.</li>
<li>The Analytics framework in MDAT analyzes the metadata from the perception engine and detects events of interest such as an anomaly. It stores this information along with the needed metadata such as start and end time.</li>
<li>The Analytics Framework then notifies the UI of this anomaly through an API.</li>
<li>Web Clients can request Media Service to fetch the anomaly clip with sensor id (camera) and start and end time.</li>
<li>Media Service sends a RTSP request to fetch the requested recorded stream from VMS and stream it to web client using webRTC peer connection</li>
</ul>
<h4><strong>Sequence Diagram to depict interactions between Web Client and Media Service</strong></h4>
<img src="doc/MS_seqdiagram2.svg" alt="MS Sequence Diagram Web Client and Media Service">

<ul>
<li>Web Client <strong><em>onLoad</em></strong> requests for all the sensor-id (cameras) to display the live / replay streams</li>
<li>On getting the list of sensor-id it sets up a <strong><em>webRTCStreamer </em></strong>instance and on demand it creates a peer connection for each</li>
<li>Web Client requests for ICE Servers using <strong><em>/api/getICEServer </em></strong>API, Media Service responds it with the list of ICE Servers.</li>
<li>Web Client then creates an offer to connect with Media Service using <strong><em>/api/call </em></strong>API, Media Service responds with an</li>
<li>Once negotiation is completed, ICE Candidates are exchanged between web client and Media Service and finally after completion of setup, web client requests for Live or Replay video stream.</li>
<li>Media Service forwards the request to RTSP Server (VMS) to get the requested stream and Media Service converts the RTSP Stream into webRTC stream and streams it to the web client.</li>
</ul>
