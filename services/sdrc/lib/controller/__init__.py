# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import time
import yaml
import requests
import json
from datetime import datetime
from flask import Flask, Response, request, stream_with_context, render_template
from flask import jsonify
from simple_settings import LazySettings
from threading import Thread
from threading import Lock
import logging
import os
from lib.logging import configure_root_logging
# from lib.podprovisioner.kubernetes.k8sclient import k8sclient
from lib.messaging.redisMessaging import redisMessaging
from lib.messaging.redis_subscriber import RedisSubscriber
from prometheus_client import Gauge, generate_latest
import threading
from kubernetes import client, watch
from kubernetes.client.rest import ApiException
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
from flask_swagger_ui import get_swaggerui_blueprint

# WDM repo root (parent of lib/); static/swagger and logs resolve here.
_CTL_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_CTL_PKG_DIR, "..", ".."))

settings = LazySettings("config")
app = Flask(
    __name__,
    static_folder=os.path.join(_REPO_ROOT, "static"),
    static_url_path="/static",
)
s = settings.Config()
app.config.from_object(s)
app.template_folder = app.config["TEMPLATE_FOLDER"]
# Logging is configured in run_workloads before import; standalone uses same formatting as app.py.
if not logging.getLogger().handlers:
    configure_root_logging("controller", _REPO_ROOT)
logger = logging.getLogger(__name__)
file_write_lock = Lock()

redisMsging = redisMessaging(app.config)
redis_connection = redisMsging.getRedisConnection()
namespace = app.config["KUBERNETS_NAMESPACE"]
configuration = client.Configuration()
configuration.api_key["authorization"] = app.config["KUBERNETES_JWT_TOKEN"]
configuration.api_key_prefix["authorization"] = "Bearer"
configuration.host = app.config["KUBERNETES_URL"]
configuration.ssl_ca_cert = app.config["SSL_CERTS"]

k8sclientCore = client.CoreV1Api(client.ApiClient(configuration))
k8sAppclientV1 = client.AppsV1Api(client.ApiClient(configuration))
agent_data_path = app.config["WDM_CONTROLLER_SDR_AGENTS_PATH"]


# swagger
SWAGGER_URL = '/api/docs'  # URL for exposing Swagger UI
API_URL = '/static/controller-swagger.json'  # URL for your swagger.json file
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={
        'app_name': "workload worker set Controller API"
    }
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)


# k8s = k8sclient(
#     app.config,
#     bearer_token=app.config["KUBERNETES_JWT_TOKEN"],
#     kubernetes_url=app.config["KUBERNETES_URL"],
#     ssl_ca_cert=app.config["SSL_CERTS"],
# )

@app.route("/healthz", methods=["GET"])
def healthz():
    return """
    OK
    """

agent_status = Gauge("agent_status", "sdr-agent status", ["agent"])
agents_connected = Gauge("agent_count", "number of sdr agents reported to the controller", ["controller"])
CONTENT_TYPE_LATEST = str('text/plain; version=0.0.4; charset=utf-8')

@app.route('/metrics')
def metrics():
    agent_data_path = app.config["WDM_CONTROLLER_SDR_AGENTS_PATH"]
    port = app.config["WDM_SDR_AGENT_PORT"]
    agent_count = 0
    try:
        with open(agent_data_path, "r") as f:
            agents_data_report = yaml.safe_load(f)
        for service_endpoint, values in agents_data_report.items():
            if values['status'] == "active":
                status = 1
            else:
                status = 0
            agent_status.labels(service_endpoint).set(status)
            agent_count += 1
        agents_connected.labels("sdr_controller").set(agent_count)
    except:
        agents_connected.labels("sdr_controller").set(0)
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def y():
    while True:
        time.sleep(10)
        localtime = time.localtime()
        result = time.strftime("%I:%M:%S %p", localtime)
        yield result


@app.route("/yy", methods=["GET"])
def yy():
    d = y()
    return Response(stream_with_context(d))

'''
Report payload from sdr agent to controller:
curl -X POST -H 'Content-Type: application/json' -d '{"service": "<AGENT_IP>"}' http://sdr-controller-service.default.svc.cluster.local:4001/report
'''
@app.route("/report", methods=["POST"])
def test():
    app.logger.info("Agent reporting endpoint called.")
    data = request.get_json()
    try:
        with open(agent_data_path, "r") as f:
            agents_data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        agents_data = {}
    sdr_service = str(data.get("service"))

    if sdr_service not in agents_data.keys():
        agent_data = {}
        agent_data[sdr_service] = {"status": "active", "port": str(data.get("port")), "activated_time": datetime.utcnow().isoformat(), "last_updated": datetime.utcnow().isoformat()}
        agents_data.update(agent_data)
    else:
        agents_data[sdr_service]["last_updated"] = datetime.utcnow().isoformat()
    #agents_data.update(agent_data)
    try:
        file_write_lock.acquire()
        with open(agent_data_path, "w") as f:
            yaml.dump(agents_data, f, default_flow_style=False)
    finally:
        file_write_lock.release()
    return request.get_json()


'''
agents-data.yaml:

<AGENT_IP>:
  activated_time: '2024-09-30T09:25:50.722923'
  last_updated: '2024-09-30T09:25:50.722923'
  status: active
'''

@app.route("/get_agent_data", methods=["GET"])
def get_agent_data():
    port = app.config["WDM_SDR_AGENT_PORT"]
    return_data = {}
    try:
        with open(agent_data_path, "r") as f:
            agents_data_report = yaml.safe_load(f)
        if agents_data_report == None:
            return "No Agent data found"
        for service_endpoint in agents_data_report.keys():
            service_data = {}
            try:
                r = requests.get("http://{}:{}/current_streamid_address_mapping".format(service_endpoint, port), timeout=3)
            except requests.exceptions.RequestException as e:
                app.logger.info("Request failed:", e)
                continue
            if r.status_code == 200:
                return_data.update({service_endpoint: {}})
                for stream, pod in r.json().items():
                    if pod in service_data:
                        service_data[pod]["streams"].append(stream)
                        service_data[pod]["wl_count"] += 1
                    else:
                        pod_add = {}
                        pod_add[pod] = {"streams": [stream], "wl_count": 1}
                        service_data.update(pod_add)
                return_data[service_endpoint] = service_data
        if return_data == {}:
            return "No active Agents found"
        return render_template('table-template.html', data=return_data)
        #return jsonify(return_data)
    except FileNotFoundError:
        return "No Agent data found"


def generateTimeStamps():
    localtime = time.localtime()
    result = time.strftime("%I:%M:%S %p", localtime)
    asyncio.sleep(10)
    return result


@app.route('/stream2')
def streamed_response2():
    def generate():
        while True:
            yield generateTimeStamps()
    return Response(stream_with_context(generate()))


@app.route('/stream', methods=["GET"])
def streamed_response():
    @stream_with_context
    def generate():
        while True:
            localtime = time.localtime()
            result = time.strftime("%I:%M:%S %p", localtime)
            readReplicas = k8s.getReadyReplicas()
            yield f"{readReplicas}"
    return Response(generate())


def podWatch():
    try:
        if redis_connection is None:
            app.logger.error("Redis connection is not available. podWatch will not start")
            return

        channel_name = app.config["WDM_ERROR_EVENT_MSG_KEY"]

        def handle_message(channel: str, data_str: str) -> None:
            try:
                payload_details = json.loads(data_str)
            except Exception:
                app.logger.debug("Non-JSON payload on %s; skipping", channel)
                return

            if not isinstance(payload_details, dict) or "payload" not in payload_details:
                app.logger.debug("Received non-reprovision message on %s; skipping", channel)
                return

            app.logger.info(f"Received reprovision message: {str(payload_details)}")
            try:
                events = payload_details["payload"]
                if not isinstance(events, list):
                    app.logger.debug("Reprovision payload not a list; skipping")
                    return


                stream_key = payload_details["msg_key"]
                msg_key = payload_details["redis_msg_field"]
                event_field = payload_details["event_field"]
                pushed = 0
                for event in events:
                    try:
                        data_json = event[event_field]
                        app.logger.info(f"data_json: {data_json}")
                    except Exception:
                        app.logger.debug("Unable to serialize event to JSON; skipping one item")
                        continue

                    try:
                        #data_json["change"] = "reprovision"
                        stream_json = {'alert_type': 'camera_status_change', 'created_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'event': data_json}
                        redis_connection.xadd(stream_key, {msg_key: json.dumps(stream_json)})
                        pushed += 1
                    except Exception:
                        app.logger.exception("Failed to XADD event to stream")


                app.logger.info(f"Reprovision forwarded {pushed}/{len(events)} events to stream '{stream_key}'")
            except Exception:
                app.logger.exception("Error while forwarding reprovision events to Redis stream")

        subscriber = RedisSubscriber(
            redis_connection,
            logger=app.logger,
            enable_signals=False,  # running in background thread
        )
        subscriber.subscribe([channel_name])
        subscriber.listen(handle_message)
    except Exception as e:
        app.logger.exception(f"Exception in podWatch Redis listener: {repr(e)}")

def PodErrorWatcher():
    app.logger.info("pod watcher thread started")
    tr = Thread(target=podWatch)
    tr.start()
    return True

'''
agents-data.yaml:

<AGENT_IP>:
  port: "4000"
  activated_time: '2024-09-30T09:25:50.722923'
  last_updated: '2024-09-30T09:25:50.722923'
  status: active
'''

def agentReportUpdate():
    # TODO
    # add file lock
    '''
    1. if status = active :
        - check the /healthz endpoint if successful - skip
        - if failed, update: 'last_updated' and status = inactive
    2. if status = inactive :
        - check the /healthz endpoint if successful - update 'last_updated' and set status = active
        - if failed, check duration between 'last_updated' and now, if more than 60 minutes, delete from report
    '''

    while True:
        app.logger.info("Checking agent report to update")
        try:
            with open(agent_data_path, "r") as f:
                agents_data_report = yaml.safe_load(f)
            if agents_data_report == None:
                return "No Agent data found"
            for agent_endpoint in list(agents_data_report.keys()):
                endpoint_available = True
                if agents_data_report[agent_endpoint]['status'] == 'active':
                    try:
                        health_check = requests.get("http://{}:{}/healthz".format(agent_endpoint, agents_data_report[agent_endpoint]["port"]), timeout=3)
                        if health_check.status_code != 200:
                            endpoint_available = False
                    except Exception as e:
                        endpoint_available = False

                    if endpoint_available == False:
                        agents_data_report[agent_endpoint]['last_updated'] = datetime.utcnow().isoformat()
                        agents_data_report[agent_endpoint]['status'] = 'inactive'
                else:
                    try:
                        health_check = requests.get("http://{}:{}/healthz".format(agent_endpoint, agents_data_report[agent_endpoint]["port"]), timeout=3)
                        if health_check.status_code != 200:
                            endpoint_available = False
                    except Exception as e:
                            endpoint_available = False
                    if endpoint_available == True:
                        agents_data_report[agent_endpoint]['last_updated'] = datetime.utcnow().isoformat()
                        agents_data_report[agent_endpoint]['status'] = 'active'
                    else:
                        time_since_inactive = (datetime.fromisoformat(agents_data_report[agent_endpoint]['last_updated']) - datetime.now()).total_seconds()
                        if time_since_inactive > 3600:
                            del agents_data_report[agent_endpoint]
            try:
                file_write_lock.acquire()
                with open(agent_data_path, "w") as f:
                    yaml.safe_dump(agents_data_report, f)
            finally:
                file_write_lock.release()


        except Exception as e:
            app.logger.info("Exception occured in checking agent endpoint: ",e)
            time.sleep(60)
            continue
        time.sleep(app.config["AGENT_CHECK_INTERVAL"])



def AgentWatcher():
    app.logger.info("pod watcher thread started")
    tr = Thread(target=agentReportUpdate)
    tr.start()
    return True

def Autoscale():

    while True:
        try:
            with open(agent_data_path, "r") as f:
                agents_data_report = yaml.safe_load(f)
            if agents_data_report == None:
                return "No Agent data found"
            for agent_endpoint, values in agents_data_report.items():
                if values['status'] == 'active':
                    try:
                        replica_data = requests.get("http://{}:{}/get_wl_replica_data".format(agent_endpoint, values["port"]), timeout=3)
                    except Exception as e:
                        app.logger.info("agent endpoint not available: ",agent_endpoint)
                        continue
                    replica_data_json = replica_data.json()
                    app.logger.info("pod status details for workload object {}: {}".format(replica_data_json["wl_object"], replica_data_json))
                    total_replicas_count = replica_data_json["total_replicas"]
                    running_pods_count =  replica_data_json["running_pods"]
                    engaged_pods_count = replica_data_json["engaged_pods"]
                    saturated_pods_count = replica_data_json["saturated_pods"]
                    pending_pods_count = replica_data_json["pending_pods"]
                    standby_pods_count = replica_data_json["standby_pods"]
                    configured_standby_pods = replica_data_json["standby_pods_configured"]

                    wl_object = replica_data_json["wl_object"]
                    active_replicas = running_pods_count + pending_pods_count
                    pods_not_running = total_replicas_count - running_pods_count

                    # make sure active replicas are not exceeding max count
                    if active_replicas >= app.config["MAX_ACTIVE_REPLICAS"]:
                        app.logger.info("Pausing scaling since max replica count reached.")
                        time.sleep(30)
                        continue

                    #if standby pods + pods in pending or restart phase is higher than standby requirement, pause scaling
                    elif standby_pods_count + pods_not_running > configured_standby_pods:
                        app.logger.info("Pausing scaling since standby pods met requirement or pods in pending state.")
                        time.sleep(15)
                        continue
                    # if standby pods < required standby pods, scale
                    elif standby_pods_count < configured_standby_pods:
                        app.logger.info("Scaling up since standby pods are less than required.")
                        try:
                            k8sAppclientV1.patch_namespaced_stateful_set_scale(
                                namespace=namespace,
                                body={"spec": {"replicas": running_pods_count + (configured_standby_pods - standby_pods_count)}},
                                name=wl_object,
                                async_req=False,
                            )
                        except ApiException as e:
                            logger.info(
                                "Exception when calling AppsV1Api->patch_namespaced_stateful_set_scale: %s\n"
                                % e
                            )

        except Exception as e:
                app.logger.info("Exception occured: ", e)
        time.sleep(app.config["AUTOSCALE_CHECK_INTERVAL"])

def Autoscaler():
    app.logger.info("autoscaler thread started")
    tr = Thread(target=Autoscale)
    tr.start()
    return True

__all__ = [
    "AgentWatcher",
    "Autoscaler",
    "PodErrorWatcher",
    "app",
    "logger",
]
